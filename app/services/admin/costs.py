"""Estimated LLM spend for the consumer product, derived from ``usage_events``.

``usage_events`` gets a row (model, tokens_in, tokens_out, feature, user) for every
AI call via UsageService, so it is the reliable source. ``llm_usage_logs`` is only
written by the offline evaluation decorator and is not used here.

All grouping is done per (dimension, model) in SQL and priced in Python, so the
queries stay portable between MySQL and SQLite.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.llm_pricing import estimate_cost, is_priced
from app.models.models import UsageEvent, User


@dataclass
class Bucket:
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    unpriced_models: set[str] = field(default_factory=set)

    def add(self, model: str, t_in: int, t_out: int, calls: int) -> None:
        self.tokens_in += int(t_in or 0)
        self.tokens_out += int(t_out or 0)
        self.calls += int(calls or 0)
        self.cost_usd += estimate_cost(model or "", int(t_in or 0), int(t_out or 0))
        if model and not is_priced(model):
            self.unpriced_models.add(model)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tokens": self.tokens_in + self.tokens_out,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "calls": self.calls,
            "cost_usd": round(self.cost_usd, 4),
        }


def _grouped(db: Session, start: datetime, end: datetime, *dims):
    q = db.query(
        *dims,
        UsageEvent.model,
        func.coalesce(func.sum(UsageEvent.tokens_in), 0),
        func.coalesce(func.sum(UsageEvent.tokens_out), 0),
        func.count(UsageEvent.id),
    ).filter(UsageEvent.created_at >= start, UsageEvent.created_at < end)
    return q.group_by(*dims, UsageEvent.model).all()


def total_cost(db: Session, start: datetime, end: datetime) -> Bucket:
    b = Bucket()
    for model, t_in, t_out, calls in _grouped(db, start, end):
        b.add(model, t_in, t_out, calls)
    return b


def cost_breakdown(db: Session, start: datetime, end: datetime, top_users: int = 10) -> dict[str, Any]:
    """Spend by model, feature, user (top N) and day for [start, end)."""
    total = Bucket()
    by_model: dict[str, Bucket] = defaultdict(Bucket)
    for model, t_in, t_out, calls in _grouped(db, start, end):
        total.add(model, t_in, t_out, calls)
        by_model[model or "unknown"].add(model, t_in, t_out, calls)

    by_feature: dict[str, Bucket] = defaultdict(Bucket)
    for feat, model, t_in, t_out, calls in _grouped(db, start, end, UsageEvent.feature):
        key = getattr(feat, "value", feat) or "other"
        by_feature[key].add(model, t_in, t_out, calls)

    by_user: dict[int, Bucket] = defaultdict(Bucket)
    for uid, model, t_in, t_out, calls in _grouped(db, start, end, UsageEvent.user_id):
        by_user[uid].add(model, t_in, t_out, calls)

    day_col = func.date(UsageEvent.created_at)
    by_day: dict[str, Bucket] = defaultdict(Bucket)
    for d, model, t_in, t_out, calls in _grouped(db, start, end, day_col):
        by_day[str(d)[:10]].add(model, t_in, t_out, calls)

    # Fill missing days so charts have a continuous axis.
    days: list[dict[str, Any]] = []
    cur = start.date()
    last = (end - timedelta(seconds=1)).date()
    today = datetime.utcnow().date()
    while cur <= min(last, today):
        key = cur.isoformat()
        days.append({"date": key, **(by_day[key].as_dict() if key in by_day else Bucket().as_dict())})
        cur += timedelta(days=1)

    top = sorted(by_user.items(), key=lambda kv: kv[1].cost_usd, reverse=True)[:top_users]
    emails = dict(db.query(User.id, User.email).filter(User.id.in_([u for u, _ in top])).all()) if top else {}

    active_users = len(by_user)
    return {
        "total": total.as_dict(),
        "active_users": active_users,
        "cost_per_active_user_usd": round(total.cost_usd / active_users, 4) if active_users else 0.0,
        "unpriced_models": sorted(total.unpriced_models),
        "by_model": sorted(
            ({"model": k, **v.as_dict()} for k, v in by_model.items()), key=lambda r: r["cost_usd"], reverse=True
        ),
        "by_feature": sorted(
            ({"feature": k, **v.as_dict()} for k, v in by_feature.items()), key=lambda r: r["cost_usd"], reverse=True
        ),
        "top_users": [{"user_id": uid, "email": emails.get(uid), **b.as_dict()} for uid, b in top],
        "by_day": days,
    }


def month_bounds(month_str: str) -> tuple[datetime, datetime]:
    year, month = (int(x) for x in month_str.split("-"))
    start = datetime(year, month, 1)
    end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return start, end


def previous_month(month_str: str) -> str:
    year, month = (int(x) for x in month_str.split("-"))
    return f"{year - (month == 1)}-{12 if month == 1 else month - 1:02d}"


def same_point_last_month(now: datetime) -> datetime:
    """The equivalent moment last month (clamped to month length) — for fair MTD comparisons."""
    prev = previous_month(now.strftime("%Y-%m"))
    start, end = month_bounds(prev)
    day = min(now.day, (end - timedelta(days=1)).day)
    return start.replace(day=day, hour=now.hour, minute=now.minute, second=now.second)



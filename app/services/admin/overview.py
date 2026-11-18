"""Operator overview: month-to-date KPIs with a fair comparison, a 30-day trend,
and a "needs attention" list of things an operator should act on today.

Consumer data only. Recruiter-side attention items (pending agencies, new
enquiries, trials ending) come from the recruiter module's own endpoints and are
merged in the admin UI — never joined here.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.enums import PlanTier, SubscriptionStatus
from app.models.models import (
    ParsedResumeData,
    SupportTicket,
    SupportTicketMessage,
    UsageEvent,
    UsageLedger,
    User,
)
from app.services.admin.costs import month_bounds, same_point_last_month, total_cost

OPEN_TICKET_STATUSES = ("open", "in_progress")
BUDGET_WARN_RATIO = 0.8
LOW_PARSE_CONFIDENCE = 0.6
LIST_LIMIT = 5


def _kpis(db: Session, now: datetime) -> dict[str, Any]:
    this_start, _ = month_bounds(now.strftime("%Y-%m"))
    prev_point = same_point_last_month(now)
    prev_start = month_bounds(prev_point.strftime("%Y-%m"))[0]

    def signups(a, b):
        return db.query(func.count(User.id)).filter(User.created_at >= a, User.created_at < b).scalar() or 0

    def active(a, b):
        return (
            db.query(func.count(func.distinct(UsageEvent.user_id)))
            .filter(UsageEvent.created_at >= a, UsageEvent.created_at < b)
            .scalar()
            or 0
        )

    cur_cost = total_cost(db, this_start, now + timedelta(seconds=1))
    prev_cost = total_cost(db, prev_start, prev_point)
    paid = db.query(func.count(User.id)).filter(User.plan != PlanTier.free, User.is_active.is_(True)).scalar() or 0

    def pair(cur, prev):
        return {"current": cur, "previous": prev}

    return {
        "users_total": db.query(func.count(User.id)).scalar() or 0,
        "paid_users": paid,
        "signups": pair(signups(this_start, now + timedelta(seconds=1)), signups(prev_start, prev_point)),
        "active_users": pair(active(this_start, now + timedelta(seconds=1)), active(prev_start, prev_point)),
        "tokens": pair(cur_cost.tokens_in + cur_cost.tokens_out, prev_cost.tokens_in + prev_cost.tokens_out),
        "ai_calls": pair(cur_cost.calls, prev_cost.calls),
        "cost_usd": pair(round(cur_cost.cost_usd, 2), round(prev_cost.cost_usd, 2)),
        "comparison_note": f"Month to date vs the same point last month ({prev_point.strftime('%d %b')})",
    }


def _trend(db: Session, now: datetime, days: int = 30) -> list[dict[str, Any]]:
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    day = func.date(User.created_at)
    signups = {str(d)[:10]: c for d, c in db.query(day, func.count(User.id)).filter(User.created_at >= start).group_by(day).all()}
    eday = func.date(UsageEvent.created_at)
    usage = {
        str(d)[:10]: (int(u or 0), int(t or 0))
        for d, u, t in db.query(
            eday,
            func.count(func.distinct(UsageEvent.user_id)),
            func.coalesce(func.sum(UsageEvent.tokens_in + UsageEvent.tokens_out), 0),
        )
        .filter(UsageEvent.created_at >= start)
        .group_by(eday)
        .all()
    }
    out = []
    for i in range(days):
        key = (start + timedelta(days=i)).date().isoformat()
        au, tok = usage.get(key, (0, 0))
        out.append({"date": key, "signups": signups.get(key, 0), "active_users": au, "tokens": tok})
    return out


def _tickets_awaiting_reply(db: Session) -> dict[str, Any]:
    last_msg = (
        db.query(SupportTicketMessage.ticket_id.label("tid"), func.max(SupportTicketMessage.id).label("mid"))
        .filter(SupportTicketMessage.deleted_at.is_(None))
        .group_by(SupportTicketMessage.ticket_id)
        .subquery()
    )
    rows = (
        db.query(SupportTicket, SupportTicketMessage.created_at, User.email)
        .join(last_msg, last_msg.c.tid == SupportTicket.id)
        .join(SupportTicketMessage, SupportTicketMessage.id == last_msg.c.mid)
        .join(User, User.id == SupportTicket.user_id)
        .filter(
            SupportTicket.status.in_(OPEN_TICKET_STATUSES),
            SupportTicket.deleted_at.is_(None),
            SupportTicketMessage.sender_role != "admin",
        )
        .order_by(SupportTicketMessage.created_at.asc())
        .all()
    )
    urgent = sum(1 for t, _, _ in rows if t.priority in ("high", "urgent"))
    return {
        "count": len(rows),
        "urgent": urgent,
        "items": [
            {"ticket_id": t.id, "subject": t.subject, "priority": t.priority, "waiting_since": since, "email": email}
            for t, since, email in rows[:LIST_LIMIT]
        ],
    }


def _users_near_budget(db: Session, now: datetime) -> dict[str, Any]:
    month = now.strftime("%Y-%m")
    q = (
        db.query(User.id, User.email, User.plan, User.token_budget_monthly, UsageLedger.tokens_used)
        .join(UsageLedger, and_(UsageLedger.user_id == User.id, UsageLedger.month_year == month))
        .filter(User.token_budget_monthly > 0, UsageLedger.tokens_used >= User.token_budget_monthly * BUDGET_WARN_RATIO)
    )
    rows = sorted(q.all(), key=lambda r: r.tokens_used / r.token_budget_monthly, reverse=True)
    return {
        "count": len(rows),
        "over_budget": sum(1 for r in rows if r.tokens_used >= r.token_budget_monthly),
        "items": [
            {
                "user_id": r.id,
                "email": r.email,
                "plan": getattr(r.plan, "value", r.plan),
                "tokens_used": r.tokens_used,
                "budget": r.token_budget_monthly,
                "pct": round(r.tokens_used / r.token_budget_monthly * 100),
            }
            for r in rows[:LIST_LIMIT]
        ],
    }


def _past_due(db: Session) -> dict[str, Any]:
    q = db.query(User.id, User.email, User.plan).filter(User.subscription_status == SubscriptionStatus.past_due)
    rows = q.order_by(User.id.desc()).all()
    return {
        "count": len(rows),
        "items": [{"user_id": r.id, "email": r.email, "plan": getattr(r.plan, "value", r.plan)} for r in rows[:LIST_LIMIT]],
    }


def _low_confidence_parses(db: Session, now: datetime) -> dict[str, Any]:
    """Latest resume parse per user in the last 30 days, where confidence is low.
    These users likely see weaker, more gap-flagged generations — worth a nudge."""
    since = now - timedelta(days=30)
    latest = (
        db.query(ParsedResumeData.user_id.label("uid"), func.max(ParsedResumeData.id).label("pid"))
        .filter(ParsedResumeData.deleted_at.is_(None), ParsedResumeData.created_at >= since)
        .group_by(ParsedResumeData.user_id)
        .subquery()
    )
    rows = (
        db.query(ParsedResumeData.user_id, ParsedResumeData.confidence_score, ParsedResumeData.created_at, User.email)
        .join(latest, latest.c.pid == ParsedResumeData.id)
        .join(User, User.id == ParsedResumeData.user_id)
        .filter(ParsedResumeData.confidence_score < LOW_PARSE_CONFIDENCE)
        .order_by(ParsedResumeData.confidence_score.asc())
        .all()
    )
    return {
        "count": len(rows),
        "threshold": LOW_PARSE_CONFIDENCE,
        "items": [
            {"user_id": uid, "email": email, "confidence": round(float(conf), 2), "parsed_at": at}
            for uid, conf, at, email in rows[:LIST_LIMIT]
        ],
    }


def build_overview(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.utcnow()
    return {
        "generated_at": now,
        "kpis": _kpis(db, now),
        "trend": _trend(db, now),
        "attention": {
            "tickets_awaiting_reply": _tickets_awaiting_reply(db),
            "users_near_budget": _users_near_budget(db, now),
            "past_due_subscriptions": _past_due(db),
            "low_confidence_parses": _low_confidence_parses(db, now),
        },
    }



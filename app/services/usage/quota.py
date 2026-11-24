"""
Per-user AI allowances: a monthly token budget and a rolling 24-hour limit.

* Monthly: ``users.token_budget_monthly`` vs ``usage_ledger.tokens_used`` for the
  current UTC month (unchanged semantics; ledger is written by metering).
* Daily: ``users.token_budget_daily`` (NULL → plan default in
  ``PLAN_DAILY_TOKEN_LIMITS``) vs LLM tokens in ``usage_events`` over the last
  24 hours. An admin reset moves ``users.usage_window_reset_at`` to now, so only
  usage after the reset counts.
* Embedding tokens are priced but never count toward either allowance.
* Admins are exempt (matches the old UsageTracker behaviour).

"Unlimited" on a plan refers to application packages, not tokens: every plan
has a token allowance and the user always sees how much of it is used.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.models import PLAN_DAILY_TOKEN_LIMITS, UsageEvent, UsageLedger, User

WINDOW = timedelta(hours=24)


class UsageLimitExceeded(HTTPException):
    def __init__(self, scope: str, used: int, limit: int, resets_at: Optional[datetime]):
        self.scope = scope
        if scope == "daily":
            error = "daily_token_limit_exceeded"
            when = f" More becomes available from {resets_at.strftime('%H:%M UTC')}." if resets_at else ""
            message = f"You've used today's AI allowance ({used:,} of {limit:,} tokens in the last 24 hours).{when}"
        else:
            error = "token_budget_exceeded"
            message = f"You have used {used:,} of your {limit:,} monthly tokens. Upgrade your plan for more."
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": error,
                "scope": scope,
                "used": used,
                "budget": limit,
                "resets_at": resets_at.isoformat() if resets_at else None,
                "message": message,
            },
        )


@dataclass
class Allowance:
    used: int
    limit: int
    resets_at: Optional[datetime]

    @property
    def pct(self) -> float:
        return round(100.0 * self.used / self.limit, 1) if self.limit > 0 else 100.0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    def as_dict(self) -> dict[str, Any]:
        return {
            "used": self.used,
            "limit": self.limit,
            "remaining": max(0, self.limit - self.used),
            "pct": self.pct,
            "exhausted": self.exhausted,
            "resets_at": self.resets_at,
        }


def daily_limit_for(user: User) -> int:
    if user.token_budget_daily is not None:
        return int(user.token_budget_daily)
    return int(PLAN_DAILY_TOKEN_LIMITS.get(user.plan, PLAN_DAILY_TOKEN_LIMITS["free"]))


def _month_end(now: datetime) -> datetime:
    last = calendar.monthrange(now.year, now.month)[1]
    return datetime(now.year, now.month, last) + timedelta(days=1)


class QuotaService:
    def __init__(self, db: Session):
        self.db = db

    # ── reads ──
    def window_start(self, user: User, now: datetime) -> datetime:
        start = now - WINDOW
        if user.usage_window_reset_at and user.usage_window_reset_at > start:
            start = user.usage_window_reset_at
        return start

    def daily(self, user: User, now: Optional[datetime] = None) -> Allowance:
        now = now or datetime.utcnow()
        start = self.window_start(user, now)
        limit = daily_limit_for(user)
        rows = (
            self.db.query(UsageEvent.created_at, UsageEvent.tokens_in + UsageEvent.tokens_out)
            .filter(
                UsageEvent.user_id == user.id,
                UsageEvent.kind == "llm",
                UsageEvent.created_at >= start,
            )
            .order_by(UsageEvent.created_at.asc())
            .all()
        )
        used = int(sum(t or 0 for _, t in rows))
        resets_at: Optional[datetime] = None
        if rows:
            if used >= limit:
                # Rolling window: allowance returns when enough old usage ages out.
                over = used - limit
                freed = 0
                for created, tokens in rows:
                    freed += int(tokens or 0)
                    if freed > over:
                        resets_at = created + WINDOW
                        break
            else:
                resets_at = rows[0][0] + WINDOW  # when the oldest counted usage drops off
        return Allowance(used=used, limit=limit, resets_at=resets_at)

    def monthly(self, user: User, now: Optional[datetime] = None) -> Allowance:
        now = now or datetime.utcnow()
        month = now.strftime("%Y-%m")
        used = (
            self.db.query(func.coalesce(func.sum(UsageLedger.tokens_used), 0))
            .filter(UsageLedger.user_id == user.id, UsageLedger.month_year == month)
            .scalar()
            or 0
        )
        return Allowance(used=int(used), limit=int(user.token_budget_monthly or 0), resets_at=_month_end(now))

    def status(self, user: User) -> dict[str, Any]:
        now = datetime.utcnow()
        daily, monthly = self.daily(user, now), self.monthly(user, now)
        exempt = user.role == UserRole.admin
        blocked = None if exempt else ("monthly" if monthly.exhausted else "daily" if daily.exhausted else None)
        return {
            "exempt": exempt,
            "blocked": blocked,
            "daily": {**daily.as_dict(), "window_hours": 24, "custom_limit": user.token_budget_daily is not None,
                      "reset_at": user.usage_window_reset_at},
            "monthly": monthly.as_dict(),
        }

    # ── enforcement ──
    def enforce(self, user_id: int, estimated_tokens: int = 0) -> None:
        user = self.db.get(User, user_id)
        if not user or user.role == UserRole.admin:
            return
        now = datetime.utcnow()
        monthly = self.monthly(user, now)
        if monthly.used + estimated_tokens > monthly.limit or monthly.exhausted:
            raise UsageLimitExceeded("monthly", monthly.used, monthly.limit, monthly.resets_at)
        daily = self.daily(user, now)
        if daily.used + estimated_tokens > daily.limit or daily.exhausted:
            raise UsageLimitExceeded("daily", daily.used, daily.limit, daily.resets_at)

    # ── admin ──
    def reset_daily(self, user: User) -> datetime:
        user.usage_window_reset_at = datetime.utcnow()
        self.db.commit()
        return user.usage_window_reset_at

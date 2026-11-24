"""
UsageTracker — records token usage per AI call and enforces monthly budgets.

Design goals:
- Minimal overhead: one upsert to usage_ledger + one insert to usage_events per call
- Budget enforcement: raises HTTP 429 before the AI call if the user is over budget
- Token counting: uses tiktoken when available to count input tokens cheaply
- Thread-safe: each request gets its own DB session
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.enums import FeatureFlag
from app.models.models import PLAN_PACKAGE_LIMITS, UsageEvent, UsageLedger, User

logger = logging.getLogger(__name__)


def _current_month() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def _count_tokens_approx(text: str, model: str = "gpt-4o-mini") -> int:
    """
    Count tokens cheaply. Uses tiktoken if available, falls back to word count.
    We intentionally keep this light — it's a best-effort pre-check.
    """
    try:
        import tiktoken
        try:
            enc = tiktoken.encoding_for_model(model)
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # No tiktoken (or its encoding files can't be fetched offline):
        # ~0.75 words per token is a reasonable approximation.
        return int(len(text.split()) * 1.3)


class UsageTracker:
    """
    Service for tracking and enforcing per-user token usage.

    Usage pattern in any AI route/service:
        tracker = UsageTracker(db, user_id, model)
        tracker.check_budget()                          # raises 429 if over limit
        result = llm.generate(system, user_prompt)
        tracker.record(feature, endpoint, tokens_in, tokens_out)
    """

    def __init__(self, db: Session, user_id: int, model: str):
        self.db = db
        self.user_id = user_id
        self.model = model

    def check_budget(self, estimated_tokens: int = 0) -> None:
        """
        Raise HTTP 429 if the user is out of monthly or rolling-24h allowance.
        Delegates to QuotaService so pre-checks and the provider meter agree.
        """
        from app.services.usage.quota import QuotaService
        QuotaService(self.db).enforce(self.user_id, estimated_tokens)

    def record(
        self,
        feature: Optional[FeatureFlag],
        endpoint: str,
        tokens_in: int,
        tokens_out: int,
    ) -> None:
        """
        Record token usage for work that did NOT go through get_llm_provider().
        Provider calls are metered automatically (MeteredLLMProvider) — calling
        this for them would double-count.
        Updates the monthly ledger (upsert) and writes a granular event row.
        Failures here are logged but never propagate to the user.
        """
        try:
            month = _current_month()
            total = tokens_in + tokens_out

            # Upsert monthly ledger
            ledger = self.db.query(UsageLedger).filter_by(user_id=self.user_id, month_year=month).first()
            if ledger:
                ledger.tokens_used += total
                ledger.api_calls += 1
            else:
                ledger = UsageLedger(
                    user_id=self.user_id,
                    month_year=month,
                    tokens_used=total,
                    api_calls=1,
                )
                self.db.add(ledger)

            # Granular event log
            event = UsageEvent(
                user_id=self.user_id,
                feature=feature,
                endpoint=endpoint,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                model=self.model,
            )
            self.db.add(event)
            self.db.commit()
        except Exception as exc:
            logger.warning("Failed to record usage event: %s", exc)
            self.db.rollback()

    def estimate_tokens(self, *texts: str) -> int:
        """Estimate the token count for one or more text strings."""
        return sum(_count_tokens_approx(t, self.model) for t in texts)

    def packages_used_this_month(self) -> int:
        """Return how many application packages this user has generated this month."""
        month = _current_month()
        ledger = self.db.query(UsageLedger).filter_by(user_id=self.user_id, month_year=month).first()
        return ledger.packages_used if ledger else 0

    def monthly_package_limit(self, user: User) -> int:
        """Return the monthly application-package limit for this user's plan (-1 = unlimited)."""
        return PLAN_PACKAGE_LIMITS.get(user.plan, 0)

    def increment_packages_used(self) -> int:
        """
        Increment the monthly package-generation counter (upsert) and return the new total.
        """
        month = _current_month()
        ledger = self.db.query(UsageLedger).filter_by(user_id=self.user_id, month_year=month).first()
        if ledger:
            ledger.packages_used += 1
        else:
            ledger = UsageLedger(user_id=self.user_id, month_year=month, packages_used=1)
            self.db.add(ledger)
        self.db.commit()
        return ledger.packages_used

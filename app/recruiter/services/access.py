"""
Agency access state (Phase 5.5).

A self-serve agency runs on a free trial; when the trial ends and there's no
active paid subscription, the tenant *locks* — reads still work so the owner can
log in and pay, but write actions are blocked (402). Operator-created agencies
have no trial clock (trial_ends_at is NULL) and are never locked here — the
operator manages them via status (active/suspended) instead.
"""
from __future__ import annotations

from datetime import datetime

from app.recruiter.models import Agency

def is_locked(agency: Agency) -> bool:
    """
    True when the free trial has ended with no active paid subscription.

    An active Stripe subscription always unlocks. Note "trialing" here is our
    *own* free-trial marker (set at signup with no Stripe subscription) — it does
    NOT unlock; the trial_ends_at clock governs it. A real Stripe trial (status
    "trialing" *with* a subscription id) does unlock.
    """
    if agency.subscription_status == "active":
        return False
    if agency.subscription_status == "trialing" and agency.stripe_subscription_id:
        return False
    if agency.trial_ends_at is None:
        return False  # operator-managed / grandfathered — never trial-locked
    return agency.trial_ends_at < datetime.utcnow()


def trial_days_left(agency: Agency) -> int | None:
    """Whole days remaining in the trial, or None if there's no trial clock."""
    if agency.trial_ends_at is None:
        return None
    delta = agency.trial_ends_at - datetime.utcnow()
    return max(0, delta.days + (1 if delta.seconds else 0)) if delta.total_seconds() > 0 else 0

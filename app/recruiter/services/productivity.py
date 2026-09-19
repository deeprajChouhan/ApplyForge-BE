"""
Phase 6: recruiter productivity — daily brief + notification generation
from process state. Nothing here auto-sends anything to candidates or clients;
it only surfaces recruiter-facing items.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.recruiter.models import (
    Application,
    CandidateConsent,
    ClientSubmission,
    Interview,
    Offer,
    Placement,
    Role,
    SubmissionFeedback,
)
from app.recruiter.services import ai_support
from app.recruiter.services.collaboration import sla_hours_for_client


def _item(priority, title, detail=None, action_kind=None, parent_kind=None, parent_id=None):
    return {
        "priority": priority, "title": title, "detail": detail,
        "action_kind": action_kind, "parent_kind": parent_kind, "parent_id": parent_id,
    }


def daily_brief(db: Session, agency_id: int, recruiter_id: int | None = None) -> dict[str, Any]:
    now = datetime.utcnow()
    urgent: list[dict] = []
    high: list[dict] = []
    medium: list[dict] = []
    opportunities: list[dict] = []

    # ── Offers expiring soon ─────────────────────────────────────────
    for o in db.query(Offer).filter(
        Offer.agency_id == agency_id,
        Offer.status.in_(["sent", "negotiating"]),
        Offer.expiry_date.isnot(None),
    ).all():
        days = (o.expiry_date - now.date()).days if o.expiry_date else None
        if days is None:
            continue
        if days <= 1:
            urgent.append(_item("urgent", f"Offer expires in {max(days, 0)} day(s)",
                                detail="Confirm response with the candidate.",
                                action_kind="review_offer", parent_kind="offer", parent_id=o.id))
        elif days <= 5:
            high.append(_item("high", f"Offer expires in {days} days",
                              parent_kind="offer", parent_id=o.id))

    # ── Client feedback overdue ──────────────────────────────────────
    for sub in db.query(ClientSubmission).filter(
        ClientSubmission.agency_id == agency_id,
        ClientSubmission.submitted_at.isnot(None),
        ClientSubmission.client_responded_at.is_(None),
    ).all():
        sla = sla_hours_for_client(db, agency_id, sub.client_id)
        if sub.submitted_at and now - sub.submitted_at > timedelta(hours=sla):
            high.append(_item(
                "high",
                f"Client feedback overdue on submission #{sub.id}",
                detail=f"Submitted {(now - sub.submitted_at).days} days ago (SLA {sla}h).",
                action_kind="chase_client", parent_kind="submission", parent_id=sub.id,
            ))

    # ── Interviews upcoming (next 48h) ──────────────────────────────
    soon = now + timedelta(hours=48)
    for iv in db.query(Interview).filter(
        Interview.agency_id == agency_id,
        Interview.status == "scheduled",
        Interview.confirmed_time.isnot(None),
        Interview.confirmed_time <= soon,
        Interview.confirmed_time >= now,
    ).all():
        high.append(_item("high", f"Interview at {iv.confirmed_time.isoformat()}",
                          action_kind="prep_interview", parent_kind="interview", parent_id=iv.id))

    # ── Screenings without consent ──────────────────────────────────
    for app_row in db.query(Application).filter(
        Application.agency_id == agency_id,
        Application.screening_completed_at.isnot(None),
    ).all():
        consent = (
            db.query(CandidateConsent)
            .filter(
                CandidateConsent.agency_id == agency_id,
                CandidateConsent.candidate_id == app_row.candidate_id,
                CandidateConsent.role_id == app_row.role_id,
                CandidateConsent.status == "confirmed",
            )
            .first()
        )
        if consent is None:
            medium.append(_item(
                "medium",
                f"Consent missing for application #{app_row.id}",
                detail="Capture consent before submitting.",
                action_kind="capture_consent", parent_kind="application", parent_id=app_row.id,
            ))

    # ── Guarantee period ending in next 30 days ────────────────────
    horizon = now.date() + timedelta(days=30)
    for pl in db.query(Placement).filter(
        Placement.agency_id == agency_id,
        Placement.status.in_(["upcoming", "started"]),
        Placement.guarantee_ends_at.isnot(None),
        Placement.guarantee_ends_at <= horizon,
    ).all():
        days_left = (pl.guarantee_ends_at - now.date()).days if pl.guarantee_ends_at else None
        opportunities.append(_item(
            "medium",
            f"Guarantee ending in {days_left} days — placement #{pl.id}",
            action_kind="checkin", parent_kind="placement", parent_id=pl.id,
        ))

    return {
        "recruiter_id": recruiter_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "urgent": urgent, "high": high, "medium": medium, "opportunities": opportunities,
        "used_llm": False,
    }

"""
Submission workflow (Recruiter OS Phase 2).

- readiness(app_row) → structured checklist of what's missing before submit
- snapshot_screening_into(submission, app_row) → freezes the screening state
- ai_draft_submission(app_row, cand, role) → LLM-drafted client-safe narrative
  grounded in candidate CV + role + screening notes. Never invents facts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.recruiter.models import (
    Application,
    CandidateConsent,
    CandidateProfile,
    ClientSubmission,
    Role,
)
from app.recruiter.services import ai_support


def readiness(db: Session, agency_id: int, app_row: Application) -> dict[str, Any]:
    """
    Returns the structured checklist described in the spec §18. Every item is
    (key, label, ok, hint). `ready` is True only if every non-consent gate is
    green — consent is captured separately and its own agency setting may
    make it optional (future).
    """
    items: list[dict[str, Any]] = []
    def add(key, label, ok, hint=None):
        items.append({"key": key, "label": label, "ok": bool(ok), "hint": hint})

    add(
        "screening_completed",
        "Screening completed",
        app_row.screening_completed_at is not None,
        "Complete the screening workflow before submitting.",
    )
    add(
        "salary_confirmed",
        "Expected compensation confirmed",
        bool(app_row.expected_compensation and app_row.expected_compensation.get("amount")),
        "Confirm the candidate's expected compensation for this role.",
    )
    add(
        "notice_confirmed",
        "Notice period confirmed",
        bool(app_row.notice_period and app_row.notice_period.get("value") is not None),
        "Confirm the candidate's notice period.",
    )
    add(
        "availability_confirmed",
        "Availability confirmed",
        bool(app_row.availability_date or app_row.availability_immediate),
        "Confirm when the candidate can start.",
    )

    # Consent — most recent for this candidate + role
    consent = (
        db.query(CandidateConsent)
        .filter(
            CandidateConsent.agency_id == agency_id,
            CandidateConsent.candidate_id == app_row.candidate_id,
            CandidateConsent.role_id == app_row.role_id,
        )
        .order_by(CandidateConsent.id.desc())
        .first()
    )
    add(
        "consent_captured",
        "Candidate consent captured",
        consent is not None and consent.status == "confirmed",
        "Capture the candidate's consent to be represented for this role.",
    )
    add(
        "client_summary_present",
        "Client-visible summary present",
        bool(app_row.recruiter_summary and app_row.recruiter_summary.strip()),
        "Write or AI-improve the recruiter summary before submitting.",
    )

    missing = sum(1 for i in items if not i["ok"])
    return {
        "application_id": app_row.id,
        "ready": missing == 0,
        "items": items,
        "missing_count": missing,
    }


def snapshot_screening_into(sub: ClientSubmission, app_row: Application) -> None:
    """Freezes the app's current screening state onto the submission row."""
    sub.compensation_snapshot = dict(app_row.expected_compensation or {})
    sub.notice_period_snapshot = dict(app_row.notice_period or {})
    sub.availability_snapshot = {
        "date": app_row.availability_date.isoformat() if app_row.availability_date else None,
        "immediate": bool(app_row.availability_immediate),
    }
    sub.motivation_snapshot = app_row.candidate_motivation
    # Fall back to the recruiter-written summary as the client-visible narrative
    # if the submission doesn't override it.
    if not sub.client_summary:
        sub.client_summary = app_row.recruiter_summary


_DRAFT_SYSTEM = (
    "You are a senior technical recruiter drafting a client submission for a "
    "specific candidate on a specific role. Ground every claim in the "
    "supplied evidence — the candidate's CV, skills, experiences, and the "
    "recruiter's approved screening notes. NEVER invent skills, dates, "
    "companies, salary, or availability the recruiter did not state. If "
    "something the client would want is missing, list it under "
    "'unsupported_claims' rather than inventing it. Return STRICT JSON only."
)

_DRAFT_SCHEMA = (
    'Return JSON: {"client_summary": string, "key_strengths": [string, ...], '
    '"potential_gaps": [string, ...], "unsupported_claims": [string, ...]}\n'
    "  - client_summary: 3-5 sentences, third person, client-safe, no bullets.\n"
    "  - key_strengths: 3-5 concrete points backed by the CV or recruiter notes.\n"
    "  - potential_gaps: honest gaps a client should know about.\n"
    "  - unsupported_claims: topics the recruiter did NOT cover.\n"
)


def ai_draft_submission(
    app_row: Application,
    cand: CandidateProfile,
    role: Role,
) -> dict[str, Any]:
    if not ai_support.llm_enabled():
        return {
            "client_summary": (app_row.recruiter_summary or "").strip(),
            "key_strengths": [s.name for s in (cand.skills or [])[:5]],
            "potential_gaps": [],
            "unsupported_claims": [],
            "used_llm": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    ctx = [
        f"Role: {role.title or 'Untitled'}"
        + (f" ({role.seniority})" if role.seniority else ""),
        f"Location: {role.location or 'unspecified'}",
        f"Required: {', '.join(role.required_skills or []) or 'unspecified'}",
        f"Preferred: {', '.join(role.preferred_skills or []) or 'unspecified'}",
        "",
        f"Candidate: {cand.full_name or 'unnamed'}",
        f"Headline: {cand.headline or ''}",
        f"Experience: {cand.years_experience or 'unknown'} years",
    ]
    if cand.summary:
        ctx.append(f"CV summary: {cand.summary[:600]}")
    if cand.skills:
        ctx.append(f"Skills: {', '.join(s.name for s in cand.skills)}")
    if app_row.recruiter_summary:
        ctx.append(f"\nRecruiter approved summary:\n{app_row.recruiter_summary}")
    if app_row.candidate_motivation:
        ctx.append(f"Motivation: {app_row.candidate_motivation}")
    if app_row.expected_compensation:
        ctx.append(f"Expected compensation: {app_row.expected_compensation}")

    data = ai_support.generate_json(_DRAFT_SYSTEM, _DRAFT_SCHEMA + "\n\nContext:\n" + "\n".join(ctx))
    if not isinstance(data, dict) or not isinstance(data.get("client_summary"), str):
        return {
            "client_summary": (app_row.recruiter_summary or "").strip(),
            "key_strengths": [],
            "potential_gaps": [],
            "unsupported_claims": [],
            "used_llm": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    return {
        "client_summary": data["client_summary"].strip(),
        "key_strengths": [str(x).strip() for x in (data.get("key_strengths") or []) if isinstance(x, str)][:6],
        "potential_gaps": [str(x).strip() for x in (data.get("potential_gaps") or []) if isinstance(x, str)][:6],
        "unsupported_claims": [str(x).strip() for x in (data.get("unsupported_claims") or []) if isinstance(x, str)][:6],
        "used_llm": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

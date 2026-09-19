"""
Phase 3: client collaboration helpers — comparison, SLA rollups, feedback
pattern detection. All read-only aggregations; nothing here mutates state.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from collections import Counter

from sqlalchemy.orm import Session

from app.recruiter.models import (
    CandidateProfile,
    Application,
    ClientSlaConfig,
    ClientSubmission,
    Role,
    SubmissionFeedback,
)
from app.recruiter.services import ai_support


DEFAULT_SLA_HOURS = 48


def sla_hours_for_client(db: Session, agency_id: int, client_id: int | None) -> int:
    if client_id is None:
        return DEFAULT_SLA_HOURS
    cfg = (
        db.query(ClientSlaConfig)
        .filter(
            ClientSlaConfig.agency_id == agency_id,
            ClientSlaConfig.client_id == client_id,
        )
        .first()
    )
    return cfg.expected_feedback_hours if cfg else DEFAULT_SLA_HOURS


def sla_summary(db: Session, agency_id: int, client_id: int) -> dict[str, Any]:
    sla = sla_hours_for_client(db, agency_id, client_id)
    subs: list[ClientSubmission] = (
        db.query(ClientSubmission)
        .filter(
            ClientSubmission.agency_id == agency_id,
            ClientSubmission.client_id == client_id,
            ClientSubmission.submitted_at.isnot(None),
        )
        .all()
    )
    total_delta = 0.0
    responded = 0
    awaiting = 0
    overdue = 0
    now = datetime.utcnow()
    threshold = timedelta(hours=sla)
    for s in subs:
        if s.client_responded_at and s.submitted_at:
            total_delta += (s.client_responded_at - s.submitted_at).total_seconds() / 3600.0
            responded += 1
        elif s.submitted_at and not s.client_responded_at:
            awaiting += 1
            if now - s.submitted_at > threshold:
                overdue += 1
    avg = (total_delta / responded) if responded else None
    return {
        "client_id": client_id,
        "expected_feedback_hours": sla,
        "average_feedback_hours": round(avg, 1) if avg is not None else None,
        "awaiting_feedback": awaiting,
        "overdue": overdue,
    }


def candidate_comparison(
    db: Session,
    agency_id: int,
    role_id: int,
    candidate_ids: list[int],
) -> dict[str, Any]:
    rows = []
    for cid in candidate_ids:
        cand = db.get(CandidateProfile, cid)
        if cand is None or cand.agency_id != agency_id:
            continue
        app_row = (
            db.query(Application)
            .filter(
                Application.agency_id == agency_id,
                Application.candidate_id == cid,
                Application.role_id == role_id,
            )
            .first()
        )
        rows.append({
            "candidate_id": cid,
            "display_name": cand.full_name,
            "fit_score": app_row.fit_score if app_row else None,
            "expected_compensation": app_row.expected_compensation if app_row else None,
            "notice_period": app_row.notice_period if app_row else None,
            "availability_immediate": bool(app_row.availability_immediate) if app_row else None,
            "top_skills": [s.name for s in (cand.skills or [])[:6]],
        })
    return {
        "role_id": role_id,
        "rows": rows,
        "ai_key_differences": _ai_key_differences(rows) if ai_support.llm_enabled() and rows else [],
        "used_llm": ai_support.llm_enabled() and bool(rows),
    }


def _ai_key_differences(rows: list[dict[str, Any]]) -> list[str]:
    if not ai_support.llm_enabled():
        return []
    system = (
        "You are helping a recruiter compare shortlisted candidates. Return "
        "3-5 short bullet points describing key differences ONLY based on the "
        "supplied data. Do NOT rank candidates or call any candidate 'best'. "
        "Return STRICT JSON: {\"differences\": [string, ...]}"
    )
    user = "Candidates:\n" + "\n".join(str(r) for r in rows)
    data = ai_support.generate_json(system, user)
    if isinstance(data, dict) and isinstance(data.get("differences"), list):
        return [str(x).strip() for x in data["differences"] if isinstance(x, str)][:5]
    return []


def feedback_pattern(db: Session, agency_id: int, role_id: int) -> dict[str, Any]:
    """Common rejection reasons for a role's submissions."""
    fbs = (
        db.query(SubmissionFeedback)
        .join(ClientSubmission, ClientSubmission.id == SubmissionFeedback.submission_id)
        .filter(
            SubmissionFeedback.agency_id == agency_id,
            ClientSubmission.role_id == role_id,
            SubmissionFeedback.decision == "reject",
        )
        .all()
    )
    counter: Counter[str] = Counter()
    for f in fbs:
        for r in (f.reasons or []):
            counter[r] += 1
    return {
        "role_id": role_id,
        "rejection_count": len(fbs),
        "common_rejection_reasons": [{"reason": k, "count": v} for k, v in counter.most_common(5)],
    }

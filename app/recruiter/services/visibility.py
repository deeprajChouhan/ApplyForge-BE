"""
Client-view serializer (Recruiter OS Phase 1).

Every client-facing endpoint MUST pass an Application through `to_client_view`
before returning it. Front-end hiding is not a security boundary; this module
is. Fields listed in `NEVER_CLIENT_VISIBLE` are stripped unconditionally.
Everything in `CLIENT_VISIBLE_FIELDS` is included only when the application's
`client_visibility` map allows it (default true unless the map sets false).
"""
from __future__ import annotations

from typing import Any

from app.recruiter.enums import (
    CLIENT_VISIBLE_FIELDS,
    DEFAULT_CLIENT_VISIBILITY,
    NEVER_CLIENT_VISIBLE,
)
from app.recruiter.models import Application


def _allowed(app_row: Application, field: str) -> bool:
    if field in NEVER_CLIENT_VISIBLE:
        return False
    if field not in CLIENT_VISIBLE_FIELDS:
        return False
    vmap = app_row.client_visibility or {}
    if field not in vmap:
        return DEFAULT_CLIENT_VISIBILITY.get(field, False)
    return bool(vmap.get(field))


def to_client_view(app_row: Application) -> dict[str, Any]:
    """
    Return only the fields a client is allowed to see for this application.
    Always includes the candidate's display identity, role linkage, and
    the recruiter's suitability-blind narrative (when allowed). Never
    includes internal_notes, current_compensation, or the suitability
    outcome itself — clients form their own judgment.
    """
    out: dict[str, Any] = {
        "application_id": app_row.id,
        "role_id": app_row.role_id,
        "candidate_id": app_row.candidate_id,
        "stage": app_row.stage.value if app_row.stage else None,
    }

    if _allowed(app_row, "recruiter_summary"):
        out["recruiter_summary"] = app_row.recruiter_summary
    if _allowed(app_row, "candidate_motivation"):
        out["candidate_motivation"] = app_row.candidate_motivation
    if _allowed(app_row, "expected_compensation"):
        out["expected_compensation"] = app_row.expected_compensation
    if _allowed(app_row, "notice_period"):
        out["notice_period"] = app_row.notice_period
    if _allowed(app_row, "availability"):
        out["availability_date"] = (
            app_row.availability_date.isoformat() if app_row.availability_date else None
        )
        out["availability_immediate"] = bool(app_row.availability_immediate)
    if _allowed(app_row, "preferred_work_model"):
        out["preferred_work_model"] = (
            app_row.preferred_work_model.value if app_row.preferred_work_model else None
        )
    if _allowed(app_row, "preferred_location"):
        out["preferred_location"] = app_row.preferred_location
    if _allowed(app_row, "relocation"):
        out["relocation"] = app_row.relocation.value if app_row.relocation else None

    return out

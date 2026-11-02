"""
Single source of truth for an Application's pipeline stage.

Every recruiter action that implies a stage (screening, client submission,
interview, offer, placement, client rejection) calls `advance_stage` here, so
the Kanban board on the role page, the global pipeline board and the stage
badges everywhere read the same `Application.stage` — no manual dragging
needed to keep them in sync.

Canonical order (UK agency flow):
    sourced → screening → submitted → interview → offer → placed
`rejected` is terminal and reachable from any active stage.

Auto-advances are forward-only: scheduling a second interview round never
drags an `offer`-stage candidate back to `interview`. Manual moves from the
board (PATCH /stage) may go anywhere — that's the recruiter's call.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.recruiter.enums import ApplicationStage
from app.recruiter.models import Application, ApplicationNote, Recruiter

STAGE_ORDER: list[ApplicationStage] = [
    ApplicationStage.sourced,
    ApplicationStage.screening,
    ApplicationStage.submitted,
    ApplicationStage.interview,
    ApplicationStage.offer,
    ApplicationStage.placed,
]
_RANK = {s: i for i, s in enumerate(STAGE_ORDER)}


def stage_rank(stage: ApplicationStage) -> int:
    """Position in the flow; rejected sorts after everything."""
    return _RANK.get(stage, len(STAGE_ORDER))


def _log(
    db: Session,
    app_row: Application,
    body: str,
    recruiter: Recruiter | None,
) -> None:
    db.add(
        ApplicationNote(
            agency_id=app_row.agency_id,
            application_id=app_row.id,
            author_recruiter_id=recruiter.id if recruiter else None,
            author_name=(recruiter.full_name or recruiter.email) if recruiter else None,
            kind="system",
            body=body,
        )
    )


def set_stage(
    db: Session,
    app_row: Application,
    target: ApplicationStage,
    *,
    recruiter: Recruiter | None = None,
    reason: str | None = None,
) -> bool:
    """Unconditional move (manual board moves). Logs the transition. Caller commits."""
    prev = app_row.stage
    app_row.last_activity_at = datetime.utcnow()
    if prev == target:
        return False
    app_row.stage = target
    body = f"Stage moved {prev.value} → {target.value}"
    if reason:
        body += f" ({reason})"
    _log(db, app_row, body, recruiter)
    return True


def advance_stage(
    db: Session,
    app_row: Application | None,
    target: ApplicationStage,
    *,
    recruiter: Recruiter | None = None,
    reason: str | None = None,
) -> bool:
    """
    Forward-only automatic move triggered by a recruiter action. A rejected
    candidate is revived only by an explicit action that implies progress
    (e.g. a new interview), which is what callers pass. Caller commits.
    """
    if app_row is None:
        return False
    current = app_row.stage
    if current == target:
        app_row.last_activity_at = datetime.utcnow()
        return False
    if current != ApplicationStage.rejected and stage_rank(current) >= stage_rank(target):
        app_row.last_activity_at = datetime.utcnow()
        return False
    return set_stage(db, app_row, target, recruiter=recruiter, reason=reason or "automatic")


def reject_if_at(
    db: Session,
    app_row: Application | None,
    at_stages: set[ApplicationStage],
    *,
    recruiter: Recruiter | None = None,
    reason: str,
) -> bool:
    """Client rejected → move to rejected, but only from the stage that decision applies to."""
    if app_row is None or app_row.stage not in at_stages:
        return False
    return set_stage(db, app_row, ApplicationStage.rejected, recruiter=recruiter, reason=reason)


def find_application(
    db: Session, agency_id: int, role_id: int | None, candidate_id: int
) -> Application | None:
    if role_id is None:
        return None
    return (
        db.query(Application)
        .filter(
            Application.agency_id == agency_id,
            Application.role_id == role_id,
            Application.candidate_id == candidate_id,
        )
        .order_by(Application.id.asc())
        .first()
    )

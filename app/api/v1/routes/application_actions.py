from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.auto_apply import ApplicationEvent
from app.models.models import JobApplication, User
from app.schemas.auto_apply import DeclineApplicationIn

# Kept in a separate router/file from applications.py (which is large and
# pre-existing) to avoid touching it. Both routers share the "/applications"
# prefix but expose distinct sub-paths (approve/decline/retry are new).
router = APIRouter(prefix="/applications", tags=["auto-apply-actions"])

TERMINAL_STAGES = {"submitted", "declined"}
RETRYABLE_STAGES = {"failed", "needs_answer"}


def _get_owned_application(
    db: Session, user: User, app_id: int
) -> JobApplication:
    application = (
        db.query(JobApplication).filter(JobApplication.id == app_id).first()
    )
    if application is None or application.user_id != user.id:
        raise HTTPException(status_code=404, detail="not found")
    return application


def _emit_event(
    db: Session,
    app_id: int,
    event_type: str,
    payload: Optional[dict] = None,
) -> None:
    event = ApplicationEvent(
        application_id=app_id,
        event_type=event_type,
        payload_json=payload or {},
        created_at=datetime.utcnow(),
    )
    db.add(event)


@router.post("/{app_id}/approve")
def approve_application(
    app_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    application = _get_owned_application(db, user, app_id)

    if application.auto_apply_stage != "awaiting_review":
        raise HTTPException(
            status_code=409, detail="application is not awaiting review"
        )

    application.auto_apply_stage = "submitting"
    _emit_event(db, app_id, "approved")
    db.commit()

    from app.services.auto_apply.dispatcher import submit_application

    submit_application.delay(app_id)

    return {"status": "submitting"}


@router.post("/{app_id}/decline")
def decline_application(
    app_id: int,
    payload: DeclineApplicationIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    application = _get_owned_application(db, user, app_id)

    if application.auto_apply_stage in TERMINAL_STAGES:
        raise HTTPException(
            status_code=409, detail="application is already in a terminal stage"
        )

    application.auto_apply_stage = "declined"
    reason = getattr(payload, "reason", None)
    _emit_event(db, app_id, "declined", {"reason": reason})
    db.commit()

    return {"status": "declined"}


@router.post("/{app_id}/retry")
def retry_application(
    app_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    application = _get_owned_application(db, user, app_id)

    if application.auto_apply_stage not in RETRYABLE_STAGES:
        raise HTTPException(
            status_code=409, detail="application is not in a retryable stage"
        )

    application.auto_apply_stage = "queued"
    _emit_event(db, app_id, "retry_requested")
    db.commit()

    from app.services.auto_apply.dispatcher import prepare_application

    prepare_application.delay(app_id)

    return {"status": "queued"}

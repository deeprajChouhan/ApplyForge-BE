from __future__ import annotations

import base64
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.auto_apply import ApplicationEvent, AutoApplyRun, AutoApplySettings
from app.models.models import JobApplication, User
from app.schemas.auto_apply import (
    ApplicationEventOut,
    AutoApplyQueueItem,
    AutoApplyQueueOut,
    AutoApplySettingsOut,
    AutoApplySettingsUpdate,
)

router = APIRouter(prefix="/auto-apply", tags=["auto-apply"])


# AutoApplyRunOut is not part of app.schemas.auto_apply (that module is owned by
# another workstream), so it is defined locally to keep this route additive-only.
class AutoApplyRunOut(BaseModel):
    id: int
    user_id: int
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    jobs_considered: Optional[int] = None
    jobs_queued: Optional[int] = None
    error_text: Optional[str] = None

    class Config:
        from_attributes = True


_DEFAULT_SETTINGS_KWARGS = dict(
    is_active=False,
    min_match_score=70,
    daily_cap=20,
    weekly_cap=100,
    fully_automatic=False,
)


def _get_or_create_settings(db: Session, user: User) -> AutoApplySettings:
    settings = (
        db.query(AutoApplySettings)
        .filter(AutoApplySettings.user_id == user.id)
        .first()
    )
    if settings is None:
        settings = AutoApplySettings(user_id=user.id, **_DEFAULT_SETTINGS_KWARGS)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _encode_cursor(updated_at: datetime, row_id: int) -> str:
    raw = f"{updated_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        ts_str, id_str = raw.split("|", 1)
        return datetime.fromisoformat(ts_str), int(id_str)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


@router.get("/settings", response_model=AutoApplySettingsOut)
def get_settings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AutoApplySettings:
    return _get_or_create_settings(db, user)


@router.put("/settings", response_model=AutoApplySettingsOut)
def update_settings(
    payload: AutoApplySettingsUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AutoApplySettings:
    settings = _get_or_create_settings(db, user)

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(settings, field, value)
    settings.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(settings)
    return settings


@router.post("/pause", response_model=AutoApplySettingsOut)
def pause_auto_apply(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AutoApplySettings:
    settings = _get_or_create_settings(db, user)
    settings.paused_at = datetime.utcnow()
    settings.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(settings)
    return settings


@router.post("/resume", response_model=AutoApplySettingsOut)
def resume_auto_apply(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AutoApplySettings:
    settings = _get_or_create_settings(db, user)
    settings.paused_at = None
    if not settings.is_active:
        settings.is_active = True
    settings.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(settings)
    return settings


@router.post("/tick-now")
def tick_now(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    from app.services.auto_apply.orchestrator import tick_user

    tick_user.delay(user.id)
    return {"queued": True}


@router.get("/queue", response_model=AutoApplyQueueOut)
def get_queue(
    stage: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AutoApplyQueueOut:
    query = db.query(JobApplication).filter(
        JobApplication.user_id == user.id,
        JobApplication.auto_apply_stage.isnot(None),
    )

    if stage:
        if stage == "declined":
            query = query.filter(JobApplication.auto_apply_stage == "failed")
        else:
            query = query.filter(JobApplication.auto_apply_stage == stage)

    if cursor:
        cur_updated_at, cur_id = _decode_cursor(cursor)
        query = query.filter(
            or_(
                JobApplication.updated_at < cur_updated_at,
                and_(
                    JobApplication.updated_at == cur_updated_at,
                    JobApplication.id < cur_id,
                ),
            )
        )

    rows = (
        query.order_by(
            JobApplication.updated_at.desc(), JobApplication.id.desc()
        )
        .limit(limit + 1)
        .all()
    )

    next_cursor: Optional[str] = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = _encode_cursor(last.updated_at, last.id)

    counts_rows = (
        db.query(JobApplication.auto_apply_stage, func.count(JobApplication.id))
        .filter(
            JobApplication.user_id == user.id,
            JobApplication.auto_apply_stage.isnot(None),
        )
        .group_by(JobApplication.auto_apply_stage)
        .all()
    )
    counts: Dict[str, int] = {stage_name: count for stage_name, count in counts_rows}
    if "failed" in counts:
        counts["declined"] = counts.get("declined", 0) + counts["failed"]

    # Build items explicitly — the ORM field is `id` but the API contract
    # exposes it as `application_id`, so a bare model_validate would fail.
    items = [
        AutoApplyQueueItem(
            application_id=row.id,
            company_name=row.company_name,
            role_title=row.role_title,
            match_score=row.match_score,
            match_reasons=(
                row.match_reasons_json
                if isinstance(row.match_reasons_json, list)
                else None
            ),
            auto_apply_stage=(
                "declined" if row.auto_apply_stage == "failed" else row.auto_apply_stage
            ),
            updated_at=row.updated_at,
            apply_url=None,  # TODO: join to jobs table to surface job.apply_url
            job_id=row.job_id,
        )
        for row in rows
    ]

    return AutoApplyQueueOut(items=items, next_cursor=next_cursor, counts=counts)


@router.get("/runs", response_model=List[AutoApplyRunOut])
def list_runs(
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[AutoApplyRun]:
    return (
        db.query(AutoApplyRun)
        .filter(AutoApplyRun.user_id == user.id)
        .order_by(AutoApplyRun.started_at.desc())
        .limit(limit)
        .all()
    )


@router.get(
    "/applications/{app_id}/events", response_model=List[ApplicationEventOut]
)
def list_application_events(
    app_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[ApplicationEvent]:
    application = (
        db.query(JobApplication).filter(JobApplication.id == app_id).first()
    )
    if application is None or application.user_id != user.id:
        raise HTTPException(status_code=404, detail="not found")

    return (
        db.query(ApplicationEvent)
        .filter(ApplicationEvent.application_id == app_id)
        .order_by(ApplicationEvent.created_at.asc())
        .all()
    )

"""
Admin controls for consumer auto-apply.

  GET  /admin/auto-apply/users                  — users with auto-apply settings + activity
  GET  /admin/users/{id}/auto-apply             — one user's auto-apply state
  POST /admin/users/{id}/auto-apply/disable     — turn it off for one user
  POST /admin/auto-apply/disable                — turn it off for many users (or all)

Turning auto-apply off sets is_active = false and paused_at = now, so neither
the user's own "resume" nor an account reactivation silently restarts it.
Applications already submitted to employers are never touched. Unsent
auto-drafts can optionally be soft-deleted (deleted_at), which is reversible.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core import config as app_config
from app.db.session import get_db
from app.models.auto_apply import AutoApplySettings
from app.models.models import JobApplication, User
from app.services.admin.audit import log_admin_action

router = APIRouter(prefix="/admin", tags=["admin"])

# Stages that never reached an employer. "submitted" is deliberately absent.
IN_FLIGHT_STAGES = ("queued", "preparing")
UNSENT_STAGES = ("queued", "preparing", "awaiting_review", "needs_answer", "failed", "cancelled")


class AutoApplyStageCounts(BaseModel):
    total: int = 0
    submitted: int = 0
    unsent: int = 0
    in_flight: int = 0
    last_24h: int = 0
    last_7d: int = 0
    last_created_at: Optional[datetime] = None


class AdminAutoApplyUserOut(BaseModel):
    user_id: int
    email: str
    user_is_active: bool
    is_active: bool
    paused_at: Optional[datetime] = None
    fully_automatic: bool = False
    daily_cap: int = 0
    weekly_cap: int = 0
    target_titles: List[str] = []
    settings_updated_at: Optional[datetime] = None
    counts: AutoApplyStageCounts


class AdminAutoApplyListOut(BaseModel):
    global_enabled: bool
    total: int
    running: int
    items: List[AdminAutoApplyUserOut]


class AdminAutoApplyStateOut(BaseModel):
    global_enabled: bool
    has_settings: bool
    user: Optional[AdminAutoApplyUserOut] = None


class DisableRequest(BaseModel):
    cancel_in_flight: bool = True
    remove_unsent_drafts: bool = False


class BulkDisableRequest(DisableRequest):
    # Explicit ids, or all=True for every user with auto-apply switched on.
    user_ids: List[int] = Field(default_factory=list, max_length=5000)
    all: bool = False


class DisableResult(BaseModel):
    users_disabled: int
    in_flight_cancelled: int
    drafts_removed: int


def _counts_by_user(db: Session, user_ids: list[int]) -> dict[int, AutoApplyStageCounts]:
    if not user_ids:
        return {}
    now = datetime.utcnow()
    stage = JobApplication.auto_apply_stage
    rows = (
        db.query(
            JobApplication.user_id,
            func.count(JobApplication.id),
            func.sum(case((stage == "submitted", 1), else_=0)),
            func.sum(case((stage.in_(UNSENT_STAGES), 1), else_=0)),
            func.sum(case((stage.in_(IN_FLIGHT_STAGES), 1), else_=0)),
            func.sum(case((JobApplication.created_at >= now - timedelta(hours=24), 1), else_=0)),
            func.sum(case((JobApplication.created_at >= now - timedelta(days=7), 1), else_=0)),
            func.max(JobApplication.created_at),
        )
        .filter(
            JobApplication.user_id.in_(user_ids),
            stage.isnot(None),
            JobApplication.deleted_at.is_(None),
        )
        .group_by(JobApplication.user_id)
        .all()
    )
    out: dict[int, AutoApplyStageCounts] = {}
    for uid, total, sub, unsent, inflight, d1, d7, last in rows:
        out[uid] = AutoApplyStageCounts(
            total=total or 0, submitted=int(sub or 0), unsent=int(unsent or 0),
            in_flight=int(inflight or 0), last_24h=int(d1 or 0), last_7d=int(d7 or 0),
            last_created_at=last,
        )
    return out


def _to_out(s: AutoApplySettings, u: User, counts: AutoApplyStageCounts | None) -> AdminAutoApplyUserOut:
    return AdminAutoApplyUserOut(
        user_id=u.id,
        email=u.email,
        user_is_active=bool(u.is_active),
        is_active=bool(s.is_active),
        paused_at=s.paused_at,
        fully_automatic=bool(s.fully_automatic),
        daily_cap=s.daily_cap or 0,
        weekly_cap=s.weekly_cap or 0,
        target_titles=list(s.target_titles_json or []),
        settings_updated_at=s.updated_at,
        counts=counts or AutoApplyStageCounts(),
    )


def _disable(db: Session, user_ids: list[int], req: DisableRequest) -> DisableResult:
    """Turn auto-apply off for the given users. Caller commits."""
    if not user_ids:
        return DisableResult(users_disabled=0, in_flight_cancelled=0, drafts_removed=0)
    now = datetime.utcnow()
    users_disabled = (
        db.query(AutoApplySettings)
        .filter(AutoApplySettings.user_id.in_(user_ids), AutoApplySettings.is_active.is_(True))
        .update({AutoApplySettings.is_active: False, AutoApplySettings.paused_at: now},
                synchronize_session=False)
    )
    # Also stamp paused_at on rows that were already off, so "resume" can't revive them.
    db.query(AutoApplySettings).filter(
        AutoApplySettings.user_id.in_(user_ids), AutoApplySettings.paused_at.is_(None)
    ).update({AutoApplySettings.paused_at: now}, synchronize_session=False)

    in_flight_cancelled = 0
    if req.cancel_in_flight:
        in_flight_cancelled = (
            db.query(JobApplication)
            .filter(
                JobApplication.user_id.in_(user_ids),
                JobApplication.auto_apply_stage.in_(IN_FLIGHT_STAGES),
                JobApplication.deleted_at.is_(None),
            )
            .update({JobApplication.auto_apply_stage: "cancelled"}, synchronize_session=False)
        )

    drafts_removed = 0
    if req.remove_unsent_drafts:
        drafts_removed = (
            db.query(JobApplication)
            .filter(
                JobApplication.user_id.in_(user_ids),
                JobApplication.auto_apply_stage.in_(UNSENT_STAGES),
                JobApplication.deleted_at.is_(None),
            )
            .update({JobApplication.deleted_at: now}, synchronize_session=False)
        )
    return DisableResult(
        users_disabled=users_disabled,
        in_flight_cancelled=in_flight_cancelled,
        drafts_removed=drafts_removed,
    )


@router.get("/auto-apply/users", response_model=AdminAutoApplyListOut)
def list_auto_apply_users(
    status: str = Query("on", pattern="^(on|off|all)$"),
    q: Optional[str] = Query(None, max_length=255),
    limit: int = Query(200, ge=1, le=1000),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    base = db.query(AutoApplySettings, User).join(User, User.id == AutoApplySettings.user_id)
    running = base.filter(AutoApplySettings.is_active.is_(True), AutoApplySettings.paused_at.is_(None)).count()
    qry = base
    if status == "on":
        qry = qry.filter(AutoApplySettings.is_active.is_(True))
    elif status == "off":
        qry = qry.filter(AutoApplySettings.is_active.is_(False))
    if q:
        qry = qry.filter(User.email.ilike(f"%{q.strip()}%"))
    total = qry.count()
    rows = qry.order_by(AutoApplySettings.updated_at.desc()).limit(limit).all()
    counts = _counts_by_user(db, [u.id for _, u in rows])
    items = [_to_out(s, u, counts.get(u.id)) for s, u in rows]
    # Worst offenders first: most auto-created applications in the last 7 days.
    items.sort(key=lambda i: (i.counts.last_7d, i.counts.total), reverse=True)
    return AdminAutoApplyListOut(
        global_enabled=app_config.settings.auto_apply_enabled,
        total=total, running=running, items=items,
    )


@router.get("/users/{user_id}/auto-apply", response_model=AdminAutoApplyStateOut)
def get_user_auto_apply(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    s = db.query(AutoApplySettings).filter(AutoApplySettings.user_id == user_id).first()
    if s is None:
        return AdminAutoApplyStateOut(global_enabled=app_config.settings.auto_apply_enabled, has_settings=False)
    counts = _counts_by_user(db, [user_id]).get(user_id)
    return AdminAutoApplyStateOut(
        global_enabled=app_config.settings.auto_apply_enabled,
        has_settings=True,
        user=_to_out(s, user, counts),
    )


@router.post("/users/{user_id}/auto-apply/disable", response_model=DisableResult)
def disable_user_auto_apply(
    user_id: int,
    payload: DisableRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    s = db.query(AutoApplySettings).filter(AutoApplySettings.user_id == user_id).first()
    before = {"is_active": bool(s.is_active), "paused": s.paused_at is not None} if s else None
    result = _disable(db, [user_id], payload)
    db.commit()
    log_admin_action(
        db, admin, "admin.user.auto_apply_disable", "user", user_id,
        before=before,
        after={"is_active": False, "paused": True},
        metadata={"email": user.email, **payload.model_dump(), **result.model_dump()},
        request=request,
    )
    return result


@router.post("/auto-apply/disable", response_model=DisableResult)
def bulk_disable_auto_apply(
    payload: BulkDisableRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.all:
        user_ids = [
            uid for (uid,) in db.query(AutoApplySettings.user_id)
            .filter(AutoApplySettings.is_active.is_(True)).all()
        ]
    else:
        user_ids = sorted(set(payload.user_ids))
    if not user_ids:
        raise HTTPException(status_code=400, detail="No users selected")
    result = _disable(db, user_ids, payload)
    db.commit()
    log_admin_action(
        db, admin, "admin.auto_apply.bulk_disable", "user", None,
        metadata={
            "all": payload.all,
            "user_count": len(user_ids),
            "user_ids": user_ids[:200],
            "cancel_in_flight": payload.cancel_in_flight,
            "remove_unsent_drafts": payload.remove_unsent_drafts,
            **result.model_dump(),
        },
        request=request,
    )
    return result

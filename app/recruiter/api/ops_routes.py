"""
Operator "Ops tools" — data repair from the admin panel instead of the DB.

Mounted at /api/v1/recruiter/admin/ops/*, platform-admin only (require_admin).
Every write is recorded in rec_integration_audit as operator:<email>, so it
shows on the agency's audit trail next to API-key and webhook changes.

Endpoints
  GET   /agencies/{agency_id}/roles                 search roles (title / careers-site ref)
  GET   /agencies/{agency_id}/duplicate-roles       roles whose titles collide
  POST  /agencies/{agency_id}/roles/merge           preview (dry_run) or apply a merge
  PATCH /agencies/{agency_id}/roles/{role_id}/external-ref   link / unlink careers-site ref
  GET   /agencies/{agency_id}/history               past ops actions for the agency
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.models import User
from app.recruiter.ids import PublicIdRoute
from app.recruiter.models import Agency, IntegrationAuditEvent
from app.recruiter.partner import audit
from app.recruiter.services import role_ops
from app.recruiter.services.role_ops import RoleOpsError

router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/admin/ops",
    tags=["recruiter: admin ops"],
    dependencies=[Depends(require_admin)],
)

OPS_ACTIONS = ("role.merged", "role.external_ref_set", "role.external_ref_cleared")


# ── Schemas ────────────────────────────────────────────────────────────
class OpsRole(BaseModel):
    id: int
    title: str
    status: str | None
    is_draft: bool
    source: str
    external_ref: str | None
    client_id: int | None
    location: str | None
    applications: int
    created_at: datetime | None


class DuplicateGroup(BaseModel):
    key: str
    title: str
    suggested_keep_id: int
    has_portal_copy: bool
    roles: list[OpsRole]


class MergeIn(BaseModel):
    source_role_id: int = Field(description="The duplicate — applications move OFF this role.")
    target_role_id: int = Field(description="The role to keep.")
    move_external_ref: bool = True
    delete_source: bool = False
    rescore: bool = True
    dry_run: bool = True


class MergeApp(BaseModel):
    application_id: int
    candidate_id: int
    candidate_name: str | None
    stage: str | None
    fit_score: float | None
    source: str | None


class MergeOut(BaseModel):
    source: OpsRole
    target: OpsRole
    move_external_ref: bool
    delete_source: bool
    rescore: bool
    applications_to_move: list[MergeApp]
    clashes: list[MergeApp]
    related_rows: dict[str, int]
    role_only_rows: dict[str, int]
    warnings: list[str]
    applied: bool
    rescored: int


class ExternalRefIn(BaseModel):
    external_ref: str | None = Field(default=None, max_length=128)


class OpsHistoryRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor: str
    action: str
    target: str | None
    detail: dict | None
    created_at: datetime | None


# ── Helpers ────────────────────────────────────────────────────────────
def _agency(db: Session, agency_id: int) -> Agency:
    a = db.get(Agency, agency_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Agency not found.")
    return a


def _actor(user: User) -> str:
    return f"operator:{user.email}"


def _raise(exc: RoleOpsError):
    raise HTTPException(status_code=exc.status, detail=str(exc))


# ── Routes ─────────────────────────────────────────────────────────────
@router.get("/agencies/{agency_id}/roles", response_model=list[OpsRole])
def ops_roles(
    agency_id: int,
    q: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
):
    _agency(db, agency_id)
    return role_ops.list_roles(db, agency_id, q)


@router.get("/agencies/{agency_id}/duplicate-roles", response_model=list[DuplicateGroup])
def ops_duplicate_roles(agency_id: int, db: Session = Depends(get_db)):
    _agency(db, agency_id)
    return role_ops.find_duplicate_roles(db, agency_id)


@router.post("/agencies/{agency_id}/roles/merge", response_model=MergeOut)
def ops_merge_roles(
    agency_id: int,
    payload: MergeIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Preview with dry_run=true (default), then call again with dry_run=false to apply."""
    _agency(db, agency_id)
    kwargs = dict(
        move_external_ref=payload.move_external_ref,
        delete_source=payload.delete_source,
        rescore=payload.rescore,
    )
    try:
        if payload.dry_run:
            plan, *_ = role_ops.plan_merge(db, agency_id, payload.source_role_id, payload.target_role_id, **kwargs)
            db.rollback()
            return plan.as_dict()
        plan = role_ops.merge(db, agency_id, payload.source_role_id, payload.target_role_id, **kwargs)
    except RoleOpsError as exc:
        db.rollback()
        _raise(exc)
    audit.record(
        db, agency_id, _actor(user), "role.merged",
        target=f"role:{payload.target_role_id}",
        detail={
            "source_role_id": payload.source_role_id,
            "source_title": plan.source["title"],
            "target_role_id": payload.target_role_id,
            "target_title": plan.target["title"],
            "moved_application_ids": [a["application_id"] for a in plan.applications_to_move],
            "clash_application_ids": [a["application_id"] for a in plan.clashes],
            "related_rows": plan.related_rows,
            "external_ref_moved": bool(payload.move_external_ref and plan.source["external_ref"]),
            "external_ref": plan.source["external_ref"],
            "source_deleted": payload.delete_source,
            "rescored": plan.rescored,
        },
    )
    db.commit()
    return plan.as_dict()


@router.patch("/agencies/{agency_id}/roles/{role_id}/external-ref", response_model=OpsRole)
def ops_set_external_ref(
    agency_id: int,
    role_id: int,
    payload: ExternalRefIn,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _agency(db, agency_id)
    try:
        role, previous = role_ops.set_external_ref(db, agency_id, role_id, payload.external_ref)
    except RoleOpsError as exc:
        db.rollback()
        _raise(exc)
    audit.record(
        db, agency_id, _actor(user),
        "role.external_ref_set" if role.external_ref else "role.external_ref_cleared",
        target=f"role:{role.id}",
        detail={"title": role.title, "previous": previous, "external_ref": role.external_ref},
    )
    db.commit()
    db.refresh(role)
    counts = role_ops.app_counts(db, [role.id])
    return role_ops.role_summary(role, counts.get(role.id, 0))


@router.get("/agencies/{agency_id}/history", response_model=list[OpsHistoryRow])
def ops_history(
    agency_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    _agency(db, agency_id)
    return (
        db.query(IntegrationAuditEvent)
        .filter(IntegrationAuditEvent.agency_id == agency_id, IntegrationAuditEvent.action.in_(OPS_ACTIONS))
        .order_by(IntegrationAuditEvent.id.desc())
        .limit(limit)
        .all()
    )

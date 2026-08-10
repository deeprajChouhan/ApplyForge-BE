"""
Operator-facing management for the recruiter platform.

Guarded by the app's existing `require_admin`, so the platform operator manages
agencies and provisions/manages recruiter logins straight from the admin panel.
Recruiter credentials are hashed with the shared security helpers and stored in
rec_recruiters.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.security import hash_password
from app.db.session import get_db
from app.models.models import User
from app.recruiter.enums import PLAN_FEATURES, AgencyStatus, default_seat_limit
from app.recruiter.models import Agency, Recruiter
from app.recruiter.schemas import (
    AgencyAdminOut,
    AgencyCreate,
    AgencyPlanUpdate,
    AgencyStatusUpdate,
    BillingSummaryOut,
    RecruiterAdminOut,
    RecruiterCreate,
    RecruiterPasswordReset,
    RecruiterUpdate,
    UsageSummaryOut,
)
from app.recruiter.services import access as access_service
from app.recruiter.services import usage as usage_service


def effective_seat_limit(agency: Agency) -> int | None:
    """Per-agency override if set, else the plan default. None = unlimited."""
    return agency.seat_limit if agency.seat_limit is not None else default_seat_limit(agency.plan)

router = APIRouter(
    prefix="/admin",
    tags=["recruiter: admin"],
    dependencies=[Depends(require_admin)],
)


def _agency_out(db: Session, agency: Agency) -> AgencyAdminOut:
    count = int(
        db.query(func.count(Recruiter.id)).filter(Recruiter.agency_id == agency.id).scalar() or 0
    )
    return AgencyAdminOut(
        id=agency.id,
        name=agency.name,
        slug=agency.slug,
        plan=agency.plan,
        billing_model=agency.billing_model,
        subscription_status=agency.subscription_status,
        status=agency.status,
        trial_ends_at=agency.trial_ends_at,
        locked=access_service.is_locked(agency),
        seat_limit=effective_seat_limit(agency),
        seats_used=count,
        features=sorted(PLAN_FEATURES.get(agency.plan, set())),
        recruiter_count=count,
        created_at=agency.created_at,
    )


def _recruiter_out(recruiter: Recruiter) -> RecruiterAdminOut:
    return RecruiterAdminOut(
        id=recruiter.id,
        agency_id=recruiter.agency_id,
        agency_name=recruiter.agency.name if recruiter.agency else None,
        email=recruiter.email,
        full_name=recruiter.full_name,
        role=recruiter.role,
        is_active=recruiter.is_active,
        created_at=recruiter.created_at,
    )


# ── Agencies ───────────────────────────────────────────────────────────────
@router.get("/agencies", response_model=list[AgencyAdminOut])
def list_agencies(db: Session = Depends(get_db)):
    agencies = db.query(Agency).order_by(Agency.name).all()
    return [_agency_out(db, a) for a in agencies]


@router.get("/agencies/{agency_id}/usage", response_model=UsageSummaryOut)
def agency_usage(agency_id: int, month: str | None = None, db: Session = Depends(get_db)):
    """This-month (or given YYYY-MM) usage rollup for an agency."""
    if db.get(Agency, agency_id) is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    s = usage_service.summary(db, agency_id, month)
    return UsageSummaryOut(agency_id=s.agency_id, month=s.month, by_kind=s.by_kind, total=s.total)


@router.patch("/agencies/{agency_id}", response_model=AgencyAdminOut)
def update_agency_plan(agency_id: int, payload: AgencyPlanUpdate, db: Session = Depends(get_db)):
    """Change an agency's plan (and optional per-agency seat override)."""
    agency = db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    agency.plan = payload.plan
    # An explicit seat_limit overrides the plan default; otherwise reset to
    # plan-default behaviour (stored NULL → effective limit follows the plan).
    agency.seat_limit = payload.seat_limit
    if payload.billing_model is not None:
        agency.billing_model = payload.billing_model
    db.commit()
    db.refresh(agency)
    return _agency_out(db, agency)


@router.get("/billing/summary", response_model=BillingSummaryOut)
def billing_summary(db: Session = Depends(get_db)):
    """Cross-agency oversight snapshot for the operator console (Phase 5.6)."""
    agencies = db.query(Agency).all()
    by_status: dict[str, int] = {}
    by_plan: dict[str, int] = {}
    pending = locked = active_subs = 0
    for a in agencies:
        by_status[a.status.value] = by_status.get(a.status.value, 0) + 1
        by_plan[a.plan.value] = by_plan.get(a.plan.value, 0) + 1
        if a.status == AgencyStatus.pending:
            pending += 1
        if access_service.is_locked(a):
            locked += 1
        if a.subscription_status == "active":
            active_subs += 1
    seats_used = int(db.query(func.count(Recruiter.id)).scalar() or 0)
    return BillingSummaryOut(
        agencies_total=len(agencies),
        by_status=by_status,
        by_plan=by_plan,
        pending_approval=pending,
        locked=locked,
        active_subscriptions=active_subs,
        seats_used=seats_used,
    )


@router.post("/agencies/{agency_id}/approve", response_model=AgencyAdminOut)
def approve_agency(agency_id: int, db: Session = Depends(get_db)):
    """Approve a pending self-serve signup so its owner can log in (Phase 5.6)."""
    agency = db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    agency.status = AgencyStatus.active
    db.commit()
    db.refresh(agency)
    return _agency_out(db, agency)


@router.patch("/agencies/{agency_id}/status", response_model=AgencyAdminOut)
def set_agency_status(agency_id: int, payload: AgencyStatusUpdate, db: Session = Depends(get_db)):
    """Operator lifecycle control: approve, suspend, or reactivate an agency."""
    agency = db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    agency.status = payload.status
    db.commit()
    db.refresh(agency)
    return _agency_out(db, agency)


@router.post("/agencies", response_model=AgencyAdminOut, status_code=status.HTTP_201_CREATED)
def create_agency(payload: AgencyCreate, db: Session = Depends(get_db)):
    if db.query(Agency).filter(Agency.slug == payload.slug).first():
        raise HTTPException(status_code=409, detail="An agency with this slug already exists")
    agency = Agency(name=payload.name, slug=payload.slug)
    db.add(agency)
    db.commit()
    db.refresh(agency)
    return _agency_out(db, agency)


# ── Recruiter logins ─────────────────────────────────────────────────────
@router.get("/recruiters", response_model=list[RecruiterAdminOut])
def list_recruiters(
    agency_id: int | None = Query(default=None),
    search: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(Recruiter)
    if agency_id is not None:
        q = q.filter(Recruiter.agency_id == agency_id)
    if search:
        like = f"%{search}%"
        q = q.filter((Recruiter.email.ilike(like)) | (Recruiter.full_name.ilike(like)))
    return [_recruiter_out(r) for r in q.order_by(Recruiter.id.desc()).all()]


@router.post("/recruiters", response_model=RecruiterAdminOut, status_code=status.HTTP_201_CREATED)
def create_recruiter(payload: RecruiterCreate, db: Session = Depends(get_db)):
    agency = db.get(Agency, payload.agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    if db.query(Recruiter).filter(Recruiter.email == payload.email).first():
        raise HTTPException(status_code=409, detail="A recruiter with this email already exists")

    # Enforce the agency's seat cap (Phase 5.1).
    limit = effective_seat_limit(agency)
    if limit is not None:
        used = db.query(func.count(Recruiter.id)).filter(Recruiter.agency_id == agency.id).scalar() or 0
        if used >= limit:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Seat limit reached for the {agency.plan.value} plan ({limit} seats). Upgrade to add more.",
            )

    recruiter = Recruiter(
        agency_id=payload.agency_id,
        email=str(payload.email),
        full_name=payload.full_name,
        role=payload.role,
        password_hash=hash_password(payload.password),
        is_active=True,
    )
    db.add(recruiter)
    db.commit()
    db.refresh(recruiter)
    return _recruiter_out(recruiter)


@router.patch("/recruiters/{recruiter_id}", response_model=RecruiterAdminOut)
def update_recruiter(recruiter_id: int, payload: RecruiterUpdate, db: Session = Depends(get_db)):
    recruiter = db.get(Recruiter, recruiter_id)
    if recruiter is None:
        raise HTTPException(status_code=404, detail="Recruiter not found")
    if payload.full_name is not None:
        recruiter.full_name = payload.full_name
    if payload.is_active is not None:
        recruiter.is_active = payload.is_active
    if payload.role is not None:
        recruiter.role = payload.role
    db.commit()
    db.refresh(recruiter)
    return _recruiter_out(recruiter)


@router.post("/recruiters/{recruiter_id}/reset-password", response_model=RecruiterAdminOut)
def reset_password(recruiter_id: int, payload: RecruiterPasswordReset, db: Session = Depends(get_db)):
    recruiter = db.get(Recruiter, recruiter_id)
    if recruiter is None:
        raise HTTPException(status_code=404, detail="Recruiter not found")
    recruiter.password_hash = hash_password(payload.password)
    db.commit()
    db.refresh(recruiter)
    return _recruiter_out(recruiter)


@router.delete("/recruiters/{recruiter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recruiter(recruiter_id: int, db: Session = Depends(get_db)):
    recruiter = db.get(Recruiter, recruiter_id)
    if recruiter is None:
        raise HTTPException(status_code=404, detail="Recruiter not found")
    db.delete(recruiter)
    db.commit()

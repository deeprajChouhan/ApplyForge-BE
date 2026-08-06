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
from app.recruiter.models import Agency, Recruiter
from app.recruiter.schemas import (
    AgencyAdminOut,
    AgencyCreate,
    RecruiterAdminOut,
    RecruiterCreate,
    RecruiterPasswordReset,
    RecruiterUpdate,
)

router = APIRouter(
    prefix="/admin",
    tags=["recruiter: admin"],
    dependencies=[Depends(require_admin)],
)


def _agency_out(db: Session, agency: Agency) -> AgencyAdminOut:
    count = (
        db.query(func.count(Recruiter.id)).filter(Recruiter.agency_id == agency.id).scalar() or 0
    )
    return AgencyAdminOut(
        id=agency.id,
        name=agency.name,
        slug=agency.slug,
        recruiter_count=int(count),
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

"""
Agency-admin tier (Phase 5.3) — owner-scoped self-service.

Lets an agency owner manage their OWN tenant from the recruiter app: see their
plan, seat usage and monthly consumption, and administer recruiter seats
(invite/deactivate/reset/remove) within the plan's seat cap. Everything is
scoped to the owner's agency, derived from their token — there is no agency_id in
the path, so an owner can never touch another agency.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import get_db
from app.recruiter.api.admin_routes import _recruiter_out, effective_seat_limit
from app.recruiter.api.deps import require_owner
from app.recruiter.enums import PLAN_FEATURES, InviteStatus, RecruiterSeatRole
from app.recruiter.models import Agency, AgencyInvite, Recruiter
from app.recruiter.schemas import (
    AgencyOverviewOut,
    BillingCheckoutRequest,
    BillingUrlOut,
    InviteCreate,
    InviteOut,
    RecruiterAdminOut,
    RecruiterPasswordReset,
    TeamMemberCreate,
    TeamMemberUpdate,
    UsageSummaryOut,
)
from app.recruiter.services import access as access_service
from app.recruiter.services import billing as billing_service
from app.recruiter.services import onboarding as onboarding_service
from app.recruiter.services import usage as usage_service

router = APIRouter(prefix="/agency", tags=["recruiter: agency-admin"])


def _agency(db: Session, owner: Recruiter) -> Agency:
    agency = db.get(Agency, owner.agency_id)
    if agency is None:  # pragma: no cover - owner always has an agency
        raise HTTPException(status_code=404, detail="Agency not found")
    return agency


@router.get("/overview", response_model=AgencyOverviewOut)
def overview(owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    agency = _agency(db, owner)
    seats_used = int(
        db.query(func.count(Recruiter.id)).filter(Recruiter.agency_id == agency.id).scalar() or 0
    )
    return AgencyOverviewOut(
        id=agency.id,
        name=agency.name,
        slug=agency.slug,
        plan=agency.plan,
        billing_model=agency.billing_model,
        subscription_status=agency.subscription_status,
        billing_enabled=billing_service.is_enabled(),
        status=agency.status,
        trial_ends_at=agency.trial_ends_at,
        trial_days_left=access_service.trial_days_left(agency),
        locked=access_service.is_locked(agency),
        seat_limit=effective_seat_limit(agency),
        seats_used=seats_used,
        features=sorted(PLAN_FEATURES.get(agency.plan, set())),
    )


@router.post("/billing/checkout", response_model=BillingUrlOut)
def billing_checkout(
    payload: BillingCheckoutRequest,
    owner: Recruiter = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """Start a Stripe checkout for the given plan, using the agency's billing model."""
    agency = _agency(db, owner)
    try:
        url = billing_service.create_checkout_session(db, agency, payload.plan)
    except billing_service.BillingError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return BillingUrlOut(url=url)


@router.post("/billing/portal", response_model=BillingUrlOut)
def billing_portal(owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    """Open the Stripe billing portal for managing/canceling the subscription."""
    agency = _agency(db, owner)
    try:
        url = billing_service.create_portal_session(db, agency)
    except billing_service.BillingError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return BillingUrlOut(url=url)


@router.get("/usage", response_model=UsageSummaryOut)
def my_usage(owner: Recruiter = Depends(require_owner), month: str | None = None, db: Session = Depends(get_db)):
    s = usage_service.summary(db, owner.agency_id, month)
    return UsageSummaryOut(agency_id=s.agency_id, month=s.month, by_kind=s.by_kind, total=s.total)


def _invite_out(invite: AgencyInvite, *, with_link: bool = False) -> InviteOut:
    return InviteOut(
        id=invite.id,
        email=invite.email,
        role=invite.role,
        status=invite.status,
        expires_at=invite.expires_at,
        created_at=invite.created_at,
        invite_url=onboarding_service.invite_link(invite.token) if with_link else None,
    )


@router.get("/invites", response_model=list[InviteOut])
def list_invites(owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    """Pending seat invites for the owner's agency."""
    invites = (
        db.query(AgencyInvite)
        .filter(
            AgencyInvite.agency_id == owner.agency_id,
            AgencyInvite.status == InviteStatus.pending.value,
        )
        .order_by(AgencyInvite.id.desc())
        .all()
    )
    return [_invite_out(i, with_link=True) for i in invites]


@router.post("/invites", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
def create_invite(payload: InviteCreate, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    """Invite a recruiter to fill a seat; returns a one-time claim link."""
    agency = _agency(db, owner)
    try:
        invite = onboarding_service.create_invite(db, agency=agency, email=str(payload.email))
    except onboarding_service.OnboardingError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _invite_out(invite, with_link=True)


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(invite_id: int, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    invite = db.get(AgencyInvite, invite_id)
    if invite is None or invite.agency_id != owner.agency_id:
        raise HTTPException(status_code=404, detail="Invite not found")
    invite.status = InviteStatus.revoked.value
    db.commit()


@router.get("/team", response_model=list[RecruiterAdminOut])
def list_team(owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    members = (
        db.query(Recruiter)
        .filter(Recruiter.agency_id == owner.agency_id)
        .order_by(Recruiter.id.desc())
        .all()
    )
    return [_recruiter_out(m) for m in members]


@router.post("/team", response_model=RecruiterAdminOut, status_code=status.HTTP_201_CREATED)
def add_member(payload: TeamMemberCreate, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    agency = _agency(db, owner)
    if db.query(Recruiter).filter(Recruiter.email == payload.email).first():
        raise HTTPException(status_code=409, detail="A recruiter with this email already exists")

    limit = effective_seat_limit(agency)
    if limit is not None:
        used = db.query(func.count(Recruiter.id)).filter(Recruiter.agency_id == agency.id).scalar() or 0
        if used >= limit:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Seat limit reached for the {agency.plan.value} plan ({limit} seats). Contact your operator to upgrade.",
            )

    member = Recruiter(
        agency_id=agency.id,
        email=str(payload.email),
        full_name=payload.full_name,
        role=RecruiterSeatRole.recruiter,  # owners can't mint other owners
        password_hash=hash_password(payload.password),
        is_active=True,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    billing_service.sync_seat_quantity(db, agency)  # per-seat billing keeps step
    return _recruiter_out(member)


def _load_member(db: Session, owner: Recruiter, member_id: int) -> Recruiter:
    member = db.get(Recruiter, member_id)
    if member is None or member.agency_id != owner.agency_id:
        raise HTTPException(status_code=404, detail="Team member not found")
    return member


@router.patch("/team/{member_id}", response_model=RecruiterAdminOut)
def update_member(
    member_id: int,
    payload: TeamMemberUpdate,
    owner: Recruiter = Depends(require_owner),
    db: Session = Depends(get_db),
):
    member = _load_member(db, owner, member_id)
    if member.id == owner.id and payload.is_active is False:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own owner account.")
    if payload.full_name is not None:
        member.full_name = payload.full_name
    if payload.is_active is not None:
        member.is_active = payload.is_active
    db.commit()
    db.refresh(member)
    return _recruiter_out(member)


@router.post("/team/{member_id}/reset-password", response_model=RecruiterAdminOut)
def reset_member_password(
    member_id: int,
    payload: RecruiterPasswordReset,
    owner: Recruiter = Depends(require_owner),
    db: Session = Depends(get_db),
):
    member = _load_member(db, owner, member_id)
    member.password_hash = hash_password(payload.password)
    db.commit()
    db.refresh(member)
    return _recruiter_out(member)


@router.delete("/team/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(member_id: int, owner: Recruiter = Depends(require_owner), db: Session = Depends(get_db)):
    member = _load_member(db, owner, member_id)
    if member.id == owner.id:
        raise HTTPException(status_code=400, detail="You cannot remove your own owner account.")
    db.delete(member)
    db.commit()
    billing_service.sync_seat_quantity(db, _agency(db, owner))

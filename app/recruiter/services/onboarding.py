"""
Self-serve onboarding (Phase 5.5).

Two flows live here:
  • signup — a prospective agency creates its own tenant + first owner account.
    Default is operator-approved: the agency lands in `pending` and can't log in
    until an operator approves it. A 14-day trial clock starts immediately.
  • invite/claim — an owner invites a recruiter by email; the recipient claims a
    one-time token to set their own password, filling a seat within the plan cap.

Everything stays inside the recruiter data wall (rec_ tables only).
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.recruiter.enums import (
    INVITE_TTL_DAYS,
    TRIAL_DAYS,
    AgencyPlan,
    AgencyStatus,
    InviteStatus,
    RecruiterSeatRole,
    default_seat_limit,
)
from app.recruiter.models import Agency, AgencyInvite, Recruiter


class OnboardingError(Exception):
    """Raised for signup/invite problems the caller maps to a 4xx."""


def slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return s or "agency"


def _unique_slug(db: Session, base: str) -> str:
    slug = base
    n = 1
    while db.query(Agency).filter(Agency.slug == slug).first() is not None:
        n += 1
        slug = f"{base}-{n}"
    return slug


def effective_seat_limit(agency: Agency) -> int | None:
    return agency.seat_limit if agency.seat_limit is not None else default_seat_limit(agency.plan)


def _active_seats(db: Session, agency_id: int) -> int:
    return int(
        db.query(func.count(Recruiter.id)).filter(Recruiter.agency_id == agency_id).scalar() or 0
    )


def _pending_invites(db: Session, agency_id: int) -> int:
    return int(
        db.query(func.count(AgencyInvite.id))
        .filter(
            AgencyInvite.agency_id == agency_id,
            AgencyInvite.status == InviteStatus.pending.value,
        )
        .scalar()
        or 0
    )


# ── Signup ─────────────────────────────────────────────────────────────────
def create_signup(
    db: Session,
    *,
    agency_name: str,
    owner_email: str,
    password: str,
    slug: str | None = None,
    owner_full_name: str | None = None,
) -> tuple[Agency, Recruiter]:
    """Create a new agency + its first owner. Returns (agency, owner)."""
    agency_name = (agency_name or "").strip()
    owner_email = (owner_email or "").strip().lower()
    if not agency_name:
        raise OnboardingError("Agency name is required.")
    if not owner_email:
        raise OnboardingError("An owner email is required.")
    if db.query(Recruiter).filter(func.lower(Recruiter.email) == owner_email).first():
        raise OnboardingError("An account with this email already exists.")

    base = slugify(slug or agency_name)
    final_slug = _unique_slug(db, base)

    # Open signups go straight to active; the default is operator-approved.
    approved = bool(settings.recruiter_signup_open)
    agency = Agency(
        name=agency_name,
        slug=final_slug,
        plan=AgencyPlan.free,
        status=AgencyStatus.active if approved else AgencyStatus.pending,
        subscription_status="trialing",
        trial_ends_at=datetime.utcnow() + timedelta(days=TRIAL_DAYS),
    )
    db.add(agency)
    db.flush()

    owner = Recruiter(
        agency_id=agency.id,
        email=owner_email,
        full_name=(owner_full_name or "").strip() or None,
        role=RecruiterSeatRole.owner,
        password_hash=hash_password(password),
        is_active=True,
    )
    db.add(owner)
    db.commit()
    db.refresh(agency)
    db.refresh(owner)
    return agency, owner


# ── Invite / claim ─────────────────────────────────────────────────────────
def invite_link(token: str) -> str:
    return f"{settings.recruiter_app_url.rstrip('/')}/invite/{token}"


def create_invite(
    db: Session, *, agency: Agency, email: str, role: RecruiterSeatRole = RecruiterSeatRole.recruiter
) -> AgencyInvite:
    email = (email or "").strip().lower()
    if not email:
        raise OnboardingError("An email is required to send an invite.")
    if db.query(Recruiter).filter(func.lower(Recruiter.email) == email).first():
        raise OnboardingError("Someone with this email already has a seat.")
    existing = (
        db.query(AgencyInvite)
        .filter(
            AgencyInvite.agency_id == agency.id,
            func.lower(AgencyInvite.email) == email,
            AgencyInvite.status == InviteStatus.pending.value,
        )
        .first()
    )
    if existing:
        raise OnboardingError("There's already a pending invite for this email.")

    # A pending invite reserves a seat, so count seats + outstanding invites.
    limit = effective_seat_limit(agency)
    if limit is not None and (_active_seats(db, agency.id) + _pending_invites(db, agency.id)) >= limit:
        raise OnboardingError(
            f"Seat limit reached for the {agency.plan.value} plan ({limit} seats). "
            "Upgrade or revoke a pending invite first."
        )

    invite = AgencyInvite(
        agency_id=agency.id,
        email=email,
        role=role,
        token=secrets.token_urlsafe(32),
        status=InviteStatus.pending.value,
        expires_at=datetime.utcnow() + timedelta(days=INVITE_TTL_DAYS),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


def get_valid_invite(db: Session, token: str) -> AgencyInvite:
    invite = db.query(AgencyInvite).filter(AgencyInvite.token == token).first()
    if invite is None:
        raise OnboardingError("This invite link is invalid.")
    if invite.status != InviteStatus.pending.value:
        raise OnboardingError("This invite has already been used or revoked.")
    if invite.expires_at is not None and invite.expires_at < datetime.utcnow():
        raise OnboardingError("This invite has expired. Ask your owner to resend it.")
    return invite


def accept_invite(db: Session, *, token: str, password: str, full_name: str | None = None) -> Recruiter:
    invite = get_valid_invite(db, token)
    agency = db.get(Agency, invite.agency_id)
    if agency is None:
        raise OnboardingError("The inviting agency no longer exists.")
    if db.query(Recruiter).filter(func.lower(Recruiter.email) == invite.email.lower()).first():
        raise OnboardingError("An account with this email already exists.")

    # Re-check the seat cap at claim time (active seats only — this invite is
    # about to convert from pending to a seat).
    limit = effective_seat_limit(agency)
    if limit is not None and _active_seats(db, agency.id) >= limit:
        raise OnboardingError("This agency is at its seat limit. Ask your owner to upgrade.")

    recruiter = Recruiter(
        agency_id=agency.id,
        email=invite.email,
        full_name=(full_name or "").strip() or None,
        role=RecruiterSeatRole(invite.role),
        password_hash=hash_password(password),
        is_active=True,
    )
    db.add(recruiter)
    invite.status = InviteStatus.accepted.value
    db.commit()
    db.refresh(recruiter)
    return recruiter

"""
Provisioning — the consumer-side half of the recruiter→consumer bridge (Section 5).

This is the single additive touchpoint: given a profile payload and a consent
token, create a real ApplyForge consumer user (free plan) and import the profile
into the consumer schema (UserProfile, Skill, WorkExperience). It lives on a code
path existing users never hit, so it cannot affect the consumer product. The
handoff is one-way — once created, the consumer owns the account.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date

from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.models.enums import FeatureFlag, PlanTier, SubscriptionStatus, UserRole
from app.models.models import (
    PLAN_DEFAULT_FEATURES,
    PLAN_TOKEN_BUDGETS,
    Skill,
    User,
    UserFeature,
    UserProfile,
    WorkExperience,
)

CONSENT_TOKEN_TYPE = "provisioning_consent"


class ProvisioningError(Exception):
    """Raised when provisioning cannot proceed (bad consent, conflict, etc.)."""


@dataclass
class ProvisionResult:
    user_id: int
    email: str
    created: bool


def verify_consent_token(token: str) -> None:
    """Ensure the caller holds a valid, purpose-scoped consent token."""
    try:
        claims = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ProvisioningError("Invalid or expired consent token") from exc
    if claims.get("type") != CONSENT_TOKEN_TYPE:
        raise ProvisioningError("Consent token has the wrong type")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def provision_user_from_profile(
    db: Session, payload: dict, consent_token: str
) -> ProvisionResult:
    verify_consent_token(consent_token)

    email = (payload.get("email") or "").strip().lower()
    if not email:
        raise ProvisioningError("An email is required to provision a user")

    if db.query(User).filter(User.email.ilike(email)).first():
        raise ProvisioningError("A consumer user with this email already exists")

    # Create a free consumer user with an unguessable password (the person sets
    # their own via the claim/reset flow).
    user = User(
        email=email,
        password_hash=hash_password(secrets.token_urlsafe(24)),
        is_active=True,
        role=UserRole.user,
        plan=PlanTier.free,
        subscription_status=SubscriptionStatus.active,
        token_budget_monthly=PLAN_TOKEN_BUDGETS[PlanTier.free],
    )
    db.add(user)
    db.flush()  # assign user.id

    # Import the profile into the consumer schema.
    db.add(
        UserProfile(
            user_id=user.id,
            full_name=payload.get("full_name"),
            headline=payload.get("headline"),
            summary=payload.get("summary"),
            location=payload.get("location"),
            phone_number=payload.get("phone"),
        )
    )

    seen_skills: set[str] = set()
    for name in payload.get("skills", []) or []:
        key = (name or "").strip().lower()
        if key and key not in seen_skills:
            seen_skills.add(key)
            db.add(Skill(user_id=user.id, name=name))

    for exp in payload.get("experiences", []) or []:
        company = (exp.get("company") or "").strip()
        role = (exp.get("role") or exp.get("title") or "").strip()
        if not company and not role:
            continue
        db.add(
            WorkExperience(
                user_id=user.id,
                company=company or "—",
                role=role or "—",
                description=exp.get("description"),
                start_date=_parse_date(exp.get("start_date")),
                end_date=_parse_date(exp.get("end_date")),
            )
        )

    # Grant the free-tier feature set, mirroring normal registration.
    for feature in PLAN_DEFAULT_FEATURES[PlanTier.free]:
        db.add(UserFeature(user_id=user.id, feature=feature, enabled=True))

    db.commit()
    return ProvisionResult(user_id=user.id, email=email, created=True)

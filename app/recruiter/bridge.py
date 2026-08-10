"""
Provisioning bridge — the recruiter-side half (Section 5).

Promotes an agency CandidateProfile into a real ApplyForge consumer user. Mints
a short-lived consent token, builds the profile payload, and hands it to the
provisioning endpoint. Because the recruiter platform is a module in the same
backend, this calls the provisioning service in-process by default; if
APPLYFORGE_PROVISIONING_URL is configured (a future split deployment), it calls
that endpoint over HTTP instead. Either way it's the one additive touchpoint —
a one-way handoff, after which the recruiter app stops tracking the person.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_token
from app.models.models import User
from app.recruiter.models import CandidateProfile
from app.services.provisioning.service import (
    CONSENT_TOKEN_TYPE,
    ProvisioningError,
    provision_user_from_profile,
)


def mint_consent_token(candidate: CandidateProfile) -> str:
    return create_token(str(candidate.id), CONSENT_TOKEN_TYPE, timedelta(hours=1))


def build_payload(candidate: CandidateProfile, email: str) -> dict:
    return {
        "email": email,
        "full_name": candidate.full_name,
        "headline": candidate.headline,
        "summary": candidate.summary,
        "location": candidate.location,
        "phone": candidate.phone,
        "skills": [s.name for s in candidate.skills],
        "experiences": [
            {
                "company": e.company,
                "title": e.title,
                "description": e.description,
                "start_date": e.start_date.isoformat() if e.start_date else None,
                "end_date": e.end_date.isoformat() if e.end_date else None,
            }
            for e in candidate.experiences
        ],
        "source": {"agency_id": candidate.agency_id, "candidate_id": candidate.id},
    }


def provision_candidate(db: Session, candidate: CandidateProfile, email: str) -> int:
    """Provision a consumer user for this candidate and return the new user id."""
    consent_token = mint_consent_token(candidate)
    payload = build_payload(candidate, email)

    if settings.applyforge_provisioning_url:
        return _provision_over_http(payload, consent_token)

    # In-process (same deployment): call the provisioning service directly.
    result = provision_user_from_profile(db, payload, consent_token)
    return result.user_id


def _provision_over_http(payload: dict, consent_token: str) -> int:
    import httpx

    key = (
        settings.applyforge_provisioning_key.get_secret_value()
        if settings.applyforge_provisioning_key
        else ""
    )
    url = settings.applyforge_provisioning_url.rstrip("/") + "/api/v1/provisioning/candidate"
    resp = httpx.post(
        url,
        json={"consent_token": consent_token, "payload": payload},
        headers={"X-Provisioning-Key": key},
        timeout=settings.ai_request_timeout_seconds,
    )
    if resp.status_code >= 400:
        raise ProvisioningError(f"Provisioning endpoint returned {resp.status_code}: {resp.text}")
    return int(resp.json()["user_id"])


def already_provisioned_email(db: Session, email: str) -> bool:
    return db.query(User).filter(User.email.ilike(email)).first() is not None

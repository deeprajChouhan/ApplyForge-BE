"""
The single additive endpoint on ApplyForge (Section 5).

Accepts a profile payload + consent token from the recruiter platform and
creates a real consumer user. Guarded by a shared provisioning key so only the
recruiter platform can call it, and only enabled when that key is configured.
It sits on a path existing users never hit.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.services.provisioning.service import (
    ProvisioningError,
    provision_user_from_profile,
)

router = APIRouter(prefix="/provisioning", tags=["provisioning"])


class ProvisionRequest(BaseModel):
    consent_token: str
    payload: dict = Field(default_factory=dict)


class ProvisionResponse(BaseModel):
    user_id: int
    email: str
    created: bool


def _require_provisioning_key(x_provisioning_key: str | None = Header(default=None)) -> None:
    configured = (
        settings.applyforge_provisioning_key.get_secret_value()
        if settings.applyforge_provisioning_key
        else None
    )
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Provisioning endpoint is not enabled",
        )
    if x_provisioning_key != configured:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid provisioning key")


@router.post("/candidate", response_model=ProvisionResponse, dependencies=[Depends(_require_provisioning_key)])
def provision_candidate(body: ProvisionRequest, db: Session = Depends(get_db)):
    try:
        result = provision_user_from_profile(db, body.payload, body.consent_token)
    except ProvisioningError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return ProvisionResponse(user_id=result.user_id, email=result.email, created=result.created)

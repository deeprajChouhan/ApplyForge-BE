"""
Recruiter API dependencies + auth.

Two principals can act on recruiter data:
  • a recruiter, via a recruiter token (type "recruiter_access"), scoped to
    exactly their own agency; and
  • a platform operator, via a consumer admin token (type "access", role=admin),
    who can act across agencies for management/oversight.

Tenant isolation is enforced in `get_agency`: a recruiter may only touch their
own agency, so one agency can never read another's pool.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import Depends, HTTPException, Path, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_token
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.models import User
from app.recruiter.models import Agency, Recruiter

RECRUITER_ACCESS = "recruiter_access"
RECRUITER_REFRESH = "recruiter_refresh"

oauth2_recruiter = OAuth2PasswordBearer(
    tokenUrl="/api/v1/recruiter/auth/login", auto_error=False
)


def issue_recruiter_tokens(recruiter_id: int) -> tuple[str, str]:
    access = create_token(
        str(recruiter_id),
        RECRUITER_ACCESS,
        timedelta(minutes=settings.access_token_exp_minutes),
    )
    refresh = create_token(
        str(recruiter_id),
        RECRUITER_REFRESH,
        timedelta(days=settings.refresh_token_exp_days),
    )
    return access, refresh


def _decode(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])


def get_current_recruiter(
    token: str | None = Depends(oauth2_recruiter),
    db: Session = Depends(get_db),
) -> Recruiter:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid recruiter credentials"
    )
    if not token:
        raise cred_exc
    try:
        payload = _decode(token)
        if payload.get("type") != RECRUITER_ACCESS:
            raise cred_exc
        recruiter_id = int(payload.get("sub"))
    except (JWTError, ValueError):
        raise cred_exc
    recruiter = db.get(Recruiter, recruiter_id)
    if recruiter is None or not recruiter.is_active:
        raise cred_exc
    return recruiter


def get_agency(
    agency_id: int = Path(..., ge=1),
    token: str | None = Depends(oauth2_recruiter),
    db: Session = Depends(get_db),
) -> Agency:
    """
    Resolve an agency-scoped request, enforcing tenant isolation.

    Accepts a recruiter token (must belong to this agency) or a consumer admin
    token (operator, may act across agencies). Any other or missing token is
    rejected.
    """
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = _decode(token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    ttype = payload.get("type")
    if ttype == RECRUITER_ACCESS:
        try:
            recruiter = db.get(Recruiter, int(payload.get("sub")))
        except (TypeError, ValueError):
            recruiter = None
        if recruiter is None or not recruiter.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid recruiter")
        if recruiter.agency_id != agency_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this agency",
            )
    elif ttype == "access":
        # Consumer token — only platform admins may act on recruiter data.
        try:
            user = db.get(User, int(payload.get("sub")))
        except (TypeError, ValueError):
            user = None
        if user is None or user.role != UserRole.admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator access required")
    else:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    agency = db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agency not found")
    return agency

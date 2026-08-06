"""Recruiter authentication — login/refresh/me. Reuses the app's shared
security helpers; recruiter credentials live in rec_recruiters."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_token, verify_password
from app.db.session import get_db
from app.recruiter.api.deps import (
    RECRUITER_ACCESS,
    RECRUITER_REFRESH,
    get_current_recruiter,
    issue_recruiter_tokens,
)
from app.recruiter.models import Recruiter
from app.recruiter.schemas import (
    RecruiterLoginRequest,
    RecruiterMe,
    RecruiterRefreshRequest,
    RecruiterTokenResponse,
)

router = APIRouter(prefix="/auth", tags=["recruiter: auth"])


@router.post("/login", response_model=RecruiterTokenResponse)
def login(payload: RecruiterLoginRequest, db: Session = Depends(get_db)):
    recruiter = db.query(Recruiter).filter(Recruiter.email == payload.email).first()
    invalid = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if recruiter is None or not recruiter.password_hash:
        raise invalid
    if not recruiter.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This recruiter account is disabled")
    if not verify_password(payload.password, recruiter.password_hash):
        raise invalid

    access, refresh = issue_recruiter_tokens(recruiter.id)
    return RecruiterTokenResponse(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=RecruiterTokenResponse)
def refresh(payload: RecruiterRefreshRequest, db: Session = Depends(get_db)):
    invalid = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    try:
        claims = jwt.decode(
            payload.refresh_token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
        if claims.get("type") != RECRUITER_REFRESH:
            raise invalid
        recruiter_id = int(claims.get("sub"))
    except (JWTError, ValueError):
        raise invalid

    recruiter = db.get(Recruiter, recruiter_id)
    if recruiter is None or not recruiter.is_active:
        raise invalid

    access = create_token(
        str(recruiter.id), RECRUITER_ACCESS, timedelta(minutes=settings.access_token_exp_minutes)
    )
    new_refresh = create_token(
        str(recruiter.id), RECRUITER_REFRESH, timedelta(days=settings.refresh_token_exp_days)
    )
    return RecruiterTokenResponse(access_token=access, refresh_token=new_refresh)


@router.get("/me", response_model=RecruiterMe)
def me(recruiter: Recruiter = Depends(get_current_recruiter)):
    return recruiter

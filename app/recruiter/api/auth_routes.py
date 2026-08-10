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
from app.recruiter.enums import AgencyStatus
from app.recruiter.models import Agency, Recruiter
from app.recruiter.schemas import (
    AgencySignupRequest,
    InviteAccept,
    InvitePublicOut,
    RecruiterLoginRequest,
    RecruiterMe,
    RecruiterRefreshRequest,
    RecruiterTokenResponse,
    SignupResult,
)
from app.recruiter.services import onboarding as onboarding_service

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

    # Agency lifecycle gate (Phase 5.5). A trial-locked agency can still log in
    # (so the owner can pay); only pending/suspended agencies are blocked here.
    agency = db.get(Agency, recruiter.agency_id)
    if agency is not None and agency.status == AgencyStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your agency is awaiting operator approval. You'll be able to sign in once it's approved.",
        )
    if agency is not None and agency.status == AgencyStatus.suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This agency has been suspended. Contact support to restore access.",
        )

    access, refresh = issue_recruiter_tokens(recruiter.id)
    return RecruiterTokenResponse(access_token=access, refresh_token=refresh)


@router.post("/signup", response_model=SignupResult, status_code=status.HTTP_201_CREATED)
def signup(payload: AgencySignupRequest, db: Session = Depends(get_db)):
    """Public self-serve signup: create an agency + its first owner (Phase 5.5)."""
    try:
        agency, _owner = onboarding_service.create_signup(
            db,
            agency_name=payload.agency_name,
            owner_email=str(payload.owner_email),
            password=payload.password,
            slug=payload.slug,
            owner_full_name=payload.owner_full_name,
        )
    except onboarding_service.OnboardingError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    pending = agency.status == AgencyStatus.pending
    message = (
        "Thanks! Your workspace is awaiting approval — we'll email you when it's ready."
        if pending
        else "Your workspace is ready. You can sign in now."
    )
    return SignupResult(
        agency_id=agency.id, status=agency.status, pending_approval=pending, message=message
    )


@router.get("/invite/{token}", response_model=InvitePublicOut)
def invite_info(token: str, db: Session = Depends(get_db)):
    """Unauthenticated: describe an invite so the claim page can render it."""
    try:
        invite = onboarding_service.get_valid_invite(db, token)
    except onboarding_service.OnboardingError as exc:
        return InvitePublicOut(agency_name="", email="", valid=False, reason=str(exc))
    agency = db.get(Agency, invite.agency_id)
    return InvitePublicOut(
        agency_name=agency.name if agency else "",
        email=invite.email,
        valid=True,
    )


@router.post("/invite/{token}/accept", response_model=RecruiterTokenResponse)
def invite_accept(token: str, payload: InviteAccept, db: Session = Depends(get_db)):
    """Claim an invite: set a password, create the seat, and log in."""
    try:
        recruiter = onboarding_service.accept_invite(
            db, token=token, password=payload.password, full_name=payload.full_name
        )
    except onboarding_service.OnboardingError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
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

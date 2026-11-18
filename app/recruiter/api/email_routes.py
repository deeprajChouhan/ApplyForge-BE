"""
Recruiter email endpoints (mounted under /api/v1/recruiter).

  GET  /me/email-preferences
  PUT  /me/email-preferences
  POST /auth/forgot-password      always 200 (no account enumeration)
  POST /auth/reset-password
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import get_db
from app.recruiter.api.deps import get_current_recruiter
from app.recruiter.enums import NotificationKind
from app.recruiter.models import Recruiter
from app.recruiter.services import email as rec_email
from app.services.email.tokens import TokenError, read_reset_token, reset_fp_matches

router = APIRouter(tags=["recruiter: email"])


class RecEmailPrefs(BaseModel):
    notifications: bool
    digests: bool
    product_updates: bool
    muted_kinds: list[str]
    available_kinds: list[str] = []


class RecEmailPrefsIn(BaseModel):
    notifications: bool | None = None
    digests: bool | None = None
    product_updates: bool | None = None
    muted_kinds: list[str] | None = None


def _out(p) -> RecEmailPrefs:
    return RecEmailPrefs(
        notifications=p.notifications, digests=p.digests, product_updates=p.product_updates,
        muted_kinds=list(p.muted_kinds or []), available_kinds=[k.value for k in NotificationKind],
    )


@router.get("/me/email-preferences", response_model=RecEmailPrefs)
def get_prefs(recruiter: Recruiter = Depends(get_current_recruiter), db: Session = Depends(get_db)):
    p = rec_email.get_prefs(db, recruiter)
    db.commit()
    return _out(p)


@router.put("/me/email-preferences", response_model=RecEmailPrefs)
def update_prefs(payload: RecEmailPrefsIn, recruiter: Recruiter = Depends(get_current_recruiter), db: Session = Depends(get_db)):
    p = rec_email.get_prefs(db, recruiter)
    data = payload.model_dump(exclude_none=True)
    if "muted_kinds" in data:
        valid = {k.value for k in NotificationKind}
        data["muted_kinds"] = sorted({k for k in data["muted_kinds"] if k in valid})
    for k, v in data.items():
        setattr(p, k, v)
    db.commit()
    return _out(p)


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


@router.post("/auth/forgot-password")
def forgot_password(payload: ForgotIn, db: Session = Depends(get_db)):
    r = db.query(Recruiter).filter(func.lower(Recruiter.email) == payload.email.lower()).first()
    if r is not None and r.is_active and r.password_hash:
        rec_email.send_password_reset(db, r)
        db.commit()
    return {"message": "If that email has a seat, a reset link is on its way."}


@router.post("/auth/reset-password")
def reset_password(payload: ResetIn, db: Session = Depends(get_db)):
    try:
        rid, fp = read_reset_token("recruiter", payload.token)
    except TokenError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    r = db.get(Recruiter, rid)
    if r is None or not r.is_active or not reset_fp_matches(fp, r.password_hash):
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")
    r.password_hash = hash_password(payload.password)
    db.commit()
    return {"message": "Password updated. You can sign in now."}

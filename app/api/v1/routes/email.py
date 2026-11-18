"""
Public / user-facing email endpoints.

  POST /webhooks/resend            Resend webhooks (svix-signed): delivery events + inbound mail
  GET  /email/preferences          consumer user's email toggles
  PUT  /email/preferences
  GET  /email/unsubscribe/info     what an unsubscribe token refers to (for the confirm page)
  POST /email/unsubscribe          one-click unsubscribe (RFC 8058) and the confirm page's button
  POST /auth/forgot-password       always 200 (no account enumeration)
  POST /auth/reset-password
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import hash_password
from app.db.session import get_db
from app.models.email import OpsEmailWebhookEvent
from app.models.models import RefreshToken, User
from app.services.email import consumer as consumer_email
from app.services.email import svix, webhooks
from app.services.email.outbox import CATEGORY_PREF
from app.services.email.tokens import TokenError, read_reset_token, read_unsub_token, reset_fp_matches

logger = logging.getLogger(__name__)
router = APIRouter(tags=["email"])


# ── Resend webhook ─────────────────────────────────────────────────────────
@router.post("/webhooks/resend", include_in_schema=False)
async def resend_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    try:
        svix.verify(
            settings.resend_webhook_secret_value,
            svix_id=request.headers.get("svix-id"),
            svix_timestamp=request.headers.get("svix-timestamp"),
            svix_signature=request.headers.get("svix-signature"),
            body=body,
        )
    except svix.WebhookVerificationError as exc:
        logger.warning("resend_webhook.rejected reason=%s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature")
    payload = json.loads(body or b"{}")
    svix_id = request.headers.get("svix-id")
    try:
        db.add(OpsEmailWebhookEvent(svix_id=svix_id, event_type=payload.get("type", "")[:60]))
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"ok": True, "duplicate": True}
    result = webhooks.handle(db, payload)
    db.commit()
    return {"ok": True, "result": result}


# ── preferences ────────────────────────────────────────────────────────────
class EmailPrefsOut(BaseModel):
    notifications: bool
    digests: bool
    product_updates: bool


class EmailPrefsIn(BaseModel):
    notifications: bool | None = None
    digests: bool | None = None
    product_updates: bool | None = None


@router.get("/email/preferences", response_model=EmailPrefsOut)
def get_preferences(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = consumer_email.get_prefs(db, user.id)
    db.commit()
    return EmailPrefsOut(notifications=p.notifications, digests=p.digests, product_updates=p.product_updates)


@router.put("/email/preferences", response_model=EmailPrefsOut)
def update_preferences(payload: EmailPrefsIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = consumer_email.get_prefs(db, user.id)
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit()
    return EmailPrefsOut(notifications=p.notifications, digests=p.digests, product_updates=p.product_updates)


# ── unsubscribe ────────────────────────────────────────────────────────────
CATEGORY_LABEL = {"notification": "notification emails", "digest": "digest emails", "lifecycle": "tips & product updates"}


@router.get("/email/unsubscribe/info")
def unsubscribe_info(token: str):
    try:
        scope, _, category = read_unsub_token(token)
    except TokenError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"scope": scope, "category": category, "label": CATEGORY_LABEL.get(category, "these emails")}


@router.post("/email/unsubscribe")
def unsubscribe(token: str, db: Session = Depends(get_db)):
    try:
        scope, sid, category = read_unsub_token(token)
    except TokenError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    attr = CATEGORY_PREF.get(category)
    if attr is None:
        raise HTTPException(status_code=400, detail="This email can't be unsubscribed from.")
    if scope == "consumer":
        if db.get(User, sid) is None:
            return {"ok": True}
        p = consumer_email.get_prefs(db, sid)
    else:
        from app.recruiter.models import Recruiter
        from app.recruiter.services import email as rec_email

        r = db.get(Recruiter, sid)
        if r is None:
            return {"ok": True}
        p = rec_email.get_prefs(db, r)
    setattr(p, attr, False)
    db.commit()
    return {"ok": True, "category": category, "label": CATEGORY_LABEL.get(category)}


# ── password reset (consumer) ─────────────────────────────────────────────
class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


@router.post("/auth/forgot-password")
def forgot_password(payload: ForgotPasswordIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower()).first() or \
        db.query(User).filter(User.email == payload.email).first()
    if user is not None and user.is_active:
        consumer_email.send_password_reset(db, user)
        db.commit()
    return {"message": "If that email has an account, a reset link is on its way."}


@router.post("/auth/reset-password")
def reset_password(payload: ResetPasswordIn, db: Session = Depends(get_db)):
    try:
        uid, fp = read_reset_token("user", payload.token)
    except TokenError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    user = db.get(User, uid)
    if user is None or not user.is_active or not reset_fp_matches(fp, user.password_hash):
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")
    user.password_hash = hash_password(payload.password)
    # Sign out everywhere: a reset usually means the old password can't be trusted.
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id, RefreshToken.revoked.is_(False)).update(
        {"revoked": True}, synchronize_session=False)
    db.commit()
    return {"message": "Password updated. You can sign in now."}

"""
Stripe webhook endpoint (Phase 5.4).

Public but signature-verified: Stripe calls it to keep an agency's subscription
state in sync (checkout completed, subscription updated/canceled). Disabled
(404-equivalent 503) unless billing is configured.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.recruiter.services import billing

router = APIRouter(prefix="/billing", tags=["recruiter: billing"])


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    db: Session = Depends(get_db),
):
    if not billing.is_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing not enabled")
    payload = await request.body()
    try:
        event = billing.construct_event(payload, stripe_signature or "")
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature")
    billing.handle_event(db, event)
    return {"received": True}

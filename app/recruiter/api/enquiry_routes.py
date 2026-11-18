"""
Website enquiries — the recruiter site's "Contact us" form and the operator inbox.

Public:   POST /recruiter/public/enquiries         (no auth; honeypot + per-IP rate limit)
Operator: GET  /recruiter/admin/enquiries          (require_admin)
          GET  /recruiter/admin/enquiries/summary
          PATCH/DELETE /recruiter/admin/enquiries/{enquiry_id}

Prospects are not agencies yet, so rows carry no agency_id and no FK: this is
an operator-level inbox inside the rec_* data wall. The submitter's IP is used
only for rate limiting and is never stored.
"""
from __future__ import annotations

from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.recruiter.ids import PublicIdRoute
from app.recruiter.models import Enquiry
from app.recruiter.partner import ratelimit
from app.recruiter.partner.deps import client_ip
from app.recruiter.schemas import (
    EnquiryAccepted,
    EnquiryCreate,
    EnquiryOut,
    EnquirySummaryOut,
    EnquiryUpdate,
)

log = structlog.get_logger(__name__)

# 5 enquiries per IP per hour is plenty for a human and dull for a bot.
ENQUIRY_RATE_LIMIT = 5
ENQUIRY_RATE_WINDOW_SECONDS = 3600
ENQUIRY_STATUSES = ("new", "contacted", "qualified", "closed", "spam")

public_router = APIRouter(route_class=PublicIdRoute, prefix="/public/enquiries", tags=["recruiter: public"])
admin_router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/admin/enquiries",
    tags=["recruiter: admin"],
    dependencies=[Depends(require_admin)],
)


@public_router.post("", response_model=EnquiryAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_enquiry(payload: EnquiryCreate, request: Request, db: Session = Depends(get_db)):
    """Accept a contact-form enquiry. Always answers 202 to the honeypot so bots
    learn nothing; real submissions are stored with status "new"."""
    allowed, _, reset = ratelimit.hit(
        f"enquiry:{ratelimit.ip_bucket(client_ip(request))}",
        ENQUIRY_RATE_LIMIT,
        ENQUIRY_RATE_WINDOW_SECONDS,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many enquiries from this network. Please email support@applyforge.co.uk instead.",
            headers={"Retry-After": str(max(1, reset - int(datetime.utcnow().timestamp())))},
        )
    if payload.website:
        log.info("enquiry.honeypot_dropped")
        return EnquiryAccepted()

    row = Enquiry(
        topic=payload.topic,
        status="new",
        full_name=payload.full_name,
        email=str(payload.email).lower(),
        company=payload.company,
        company_type=payload.company_type,
        team_size=payload.team_size,
        phone=payload.phone,
        message=payload.message,
        source_page=payload.source_page,
    )
    db.add(row)
    db.commit()
    log.info("enquiry.created", enquiry_id=row.id, topic=row.topic, company_type=row.company_type)
    return EnquiryAccepted()


@admin_router.get("", response_model=list[EnquiryOut])
def list_enquiries(
    status_: str | None = Query(default=None, alias="status"),
    topic: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(Enquiry)
    if status_:
        q = q.filter(Enquiry.status == status_)
    if topic:
        q = q.filter(Enquiry.topic == topic)
    if search:
        like = f"%{search.strip().lower()}%"
        q = q.filter(
            or_(
                func.lower(Enquiry.full_name).like(like),
                func.lower(Enquiry.email).like(like),
                func.lower(func.coalesce(Enquiry.company, "")).like(like),
            )
        )
    return q.order_by(Enquiry.created_at.desc(), Enquiry.id.desc()).offset(offset).limit(limit).all()


@admin_router.get("/summary", response_model=EnquirySummaryOut)
def enquiry_summary(db: Session = Depends(get_db)):
    by_status = {s: 0 for s in ENQUIRY_STATUSES}
    for s, n in db.query(Enquiry.status, func.count(Enquiry.id)).group_by(Enquiry.status).all():
        by_status[s] = int(n)
    by_topic = {
        t: int(n) for t, n in db.query(Enquiry.topic, func.count(Enquiry.id)).group_by(Enquiry.topic).all()
    }
    return EnquirySummaryOut(
        total=sum(by_status.values()), new=by_status.get("new", 0), by_status=by_status, by_topic=by_topic
    )


def _get(db: Session, enquiry_id: int) -> Enquiry:
    row = db.get(Enquiry, enquiry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    return row


@admin_router.patch("/{enquiry_id}", response_model=EnquiryOut)
def update_enquiry(enquiry_id: int, payload: EnquiryUpdate, db: Session = Depends(get_db)):
    row = _get(db, enquiry_id)
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        if row.status == "new" and data["status"] != "new" and row.handled_at is None:
            row.handled_at = datetime.utcnow()
        row.status = data["status"]
    if "admin_notes" in data:
        notes = (data["admin_notes"] or "").strip()
        row.admin_notes = notes or None
    db.commit()
    db.refresh(row)
    return row


@admin_router.delete("/{enquiry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_enquiry(enquiry_id: int, db: Session = Depends(get_db)):
    """Hard delete — for spam and for erasure requests from the person who wrote in."""
    row = _get(db, enquiry_id)
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

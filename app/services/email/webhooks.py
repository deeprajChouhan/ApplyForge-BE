"""Resend webhook processing: delivery events → send logs + suppression list; inbound → mailbox."""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.email import OpsEmailSuppression, OpsMailboxMessage
from app.services.email import dispatch
from app.services.email.outbox import normalise

logger = logging.getLogger(__name__)

STATUS_RANK = {"queued": 0, "sending": 1, "sent": 2, "delivered": 3, "opened": 4, "clicked": 5}


def suppress(db: Session, email: str, reason: str, note: str | None = None) -> None:
    e = normalise(email)
    if e and not db.query(OpsEmailSuppression.id).filter(OpsEmailSuppression.email == e).first():
        db.add(OpsEmailSuppression(email=e, reason=reason, note=(note or "")[:300] or None))


def _rows_for(db: Session, provider_id: str) -> list:
    rows: list = []
    for cfg in dispatch.SCOPES.values():
        rows += db.query(cfg.model).filter(cfg.model.provider_message_id == provider_id).all()
    rows += db.query(OpsMailboxMessage).filter(OpsMailboxMessage.resend_email_id == provider_id,
                                               OpsMailboxMessage.direction == "outbound").all()
    return rows


def _advance(row, status: str) -> None:
    cur = row.status
    if cur in ("bounced", "complained", "failed", "suppressed"):
        return
    if STATUS_RANK.get(status, 99) >= STATUS_RANK.get(cur, -1):
        row.status = status


def handle(db: Session, payload: dict) -> str:
    etype = payload.get("type", "")
    data = payload.get("data") or {}

    if etype == "email.received":
        from app.services.email.mailbox import ingest_received

        msg = ingest_received(db, data)
        return "ingested" if msg else "ignored"

    pid = data.get("email_id")
    if not pid:
        return "no_email_id"
    rows = _rows_for(db, pid)
    now = datetime.utcnow()
    to = (data.get("to") or [None])[0]
    for row in rows:
        is_log = hasattr(row, "delivered_at")
        if etype == "email.delivered":
            _advance(row, "delivered")
            if is_log:
                row.delivered_at = row.delivered_at or now
        elif etype == "email.opened":
            if is_log:
                row.opened_at = row.opened_at or now
                _advance(row, "opened")
        elif etype == "email.clicked":
            if is_log:
                row.clicked_at = row.clicked_at or now
                _advance(row, "clicked")
        elif etype == "email.bounced":
            bounce = data.get("bounce") or {}
            row.status, row.error = "bounced", (bounce.get("message") or "bounced")[:2000]
        elif etype == "email.complained":
            row.status = "complained"
        elif etype in ("email.failed", "email.suppressed"):
            row.status = "failed" if etype == "email.failed" else "suppressed"
            row.error = str(data.get("failed") or data.get("reason") or etype)[:2000]

    if etype == "email.bounced" and to:
        bounce = data.get("bounce") or {}
        if str(bounce.get("type", "")).lower() != "transient":  # hard bounce → never retry this address
            suppress(db, to, "bounced", bounce.get("message"))
    elif etype == "email.complained" and to:
        suppress(db, to, "complained")
    return f"updated:{len(rows)}"

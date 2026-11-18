"""
Send queued email rows after the transaction that created them commits.

EMAIL_JOBS_BACKEND:
  sync    send in-process right after commit (tests / scripts)
  inline  send on a small background thread pool (dev, single container)
  celery  hand to the Celery worker (production)

A sweep (every minute: Celery beat or APScheduler) picks up delayed rows
(send_after in the future), retries and anything a crash left behind.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
_PENDING_KEY = "email_dispatch_pending"
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="email")


@dataclass
class ScopeConfig:
    model: type
    default_from: Callable[[], str]
    # (db, row) -> facts dict for cancel_if re-checks; None = no facts
    facts_for_row: Callable[[Session, Any], dict] | None = None


SCOPES: dict[str, ScopeConfig] = {}


def register_scope(name: str, cfg: ScopeConfig) -> None:
    SCOPES[name] = cfg


def mark_for_dispatch(db: Session, scope: str, row_id: int, *, delayed: bool = False) -> None:
    if delayed:
        return  # the sweep will pick it up when due
    db.info.setdefault(_PENDING_KEY, []).append((scope, row_id))


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    items = session.info.pop(_PENDING_KEY, None)
    if items:
        try:
            bind = session.get_bind()
        except Exception:  # noqa: BLE001
            bind = None
        schedule(items, bind=bind)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


def schedule(items: list[tuple[str, int]], bind=None) -> None:
    backend = (settings.email_jobs_backend or "inline").lower()
    for scope, row_id in items:
        if backend == "sync":
            send_one(scope, row_id, bind=bind)
        elif backend == "celery":
            try:
                from app.services.email.tasks import send_email_task

                send_email_task.delay(scope, row_id)
            except Exception as exc:  # broker down → sweep retries later
                logger.warning("email.celery_enqueue_failed scope=%s id=%s err=%s", scope, row_id, exc)
        else:
            _pool.submit(_safe_send, scope, row_id, bind)


def _safe_send(scope: str, row_id: int, bind=None) -> None:
    try:
        send_one(scope, row_id, bind=bind)
    except Exception:  # noqa: BLE001
        logger.exception("email.send_crashed scope=%s id=%s", scope, row_id)


def _headers(row: Any) -> dict[str, str]:
    ctx = row.context or {}
    h: dict[str, str] = {}
    page, one_click = ctx.get("_unsub_page"), ctx.get("_unsub_one_click")
    if one_click:
        h["List-Unsubscribe"] = f"<{one_click}>"
        h["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    elif page:
        h["List-Unsubscribe"] = f"<{page}>"
    return h


def _session(bind=None) -> Session:
    if bind is not None:
        return Session(bind=bind, autoflush=False)
    from app.db.session import SessionLocal

    return SessionLocal()


def send_one(scope: str, row_id: int, bind=None) -> str:
    from app.models.email import OpsEmailRule
    from app.services.email import conditions
    from app.services.email.outbox import is_suppressed
    from app.services.email.provider import EmailSendError, OutgoingEmail, get_email_provider

    cfg = SCOPES.get(scope)
    if cfg is None:
        logger.error("email.unknown_scope %s", scope)
        return "unknown_scope"
    model = cfg.model
    db = _session(bind)
    try:
        now = datetime.utcnow()
        claimed = (
            db.query(model)
            .filter(model.id == row_id, model.status == "queued", model.send_after <= now)
            .update({"status": "sending", "attempts": model.attempts + 1}, synchronize_session=False)
        )
        db.commit()
        if not claimed:
            return "not_claimed"
        row = db.get(model, row_id)

        if is_suppressed(db, row.to_email):
            row.status, row.error = "suppressed", "address is on the suppression list"
            db.commit()
            return "suppressed"

        if row.rule_id and cfg.facts_for_row:
            rule = db.get(OpsEmailRule, row.rule_id)
            if rule is not None and (not rule.is_active or (rule.cancel_if and conditions.evaluate(
                rule.cancel_if, {"facts": cfg.facts_for_row(db, row), "event": (row.context or {}).get("_event") or {}}
            ))):
                row.status, row.error = "cancelled", ("rule disabled" if not rule.is_active else "cancel condition met")
                db.commit()
                return "cancelled"

        ctx = row.context or {}
        reply_to = getattr(row, "reply_to", None) or ctx.get("_reply_to") or settings.email_reply_to
        msg = OutgoingEmail(
            from_addr=ctx.get("_from") or cfg.default_from(),
            to=[row.to_email],
            subject=row.subject,
            html=row.html or "",
            text=row.text,
            reply_to=[reply_to] if reply_to else None,
            headers=_headers(row),
            tags={"scope": scope, "template": row.template_key, "category": row.category},
            idempotency_key=f"{scope}-{row.idempotency_key}"[:256],
        )
        try:
            pid = get_email_provider().send(msg)
        except EmailSendError as exc:
            row.error = str(exc)[:2000]
            if exc.retryable and row.attempts < MAX_ATTEMPTS:
                row.status = "queued"
                row.send_after = datetime.utcnow() + timedelta(minutes=2 ** row.attempts)
            else:
                row.status = "failed"
            db.commit()
            logger.warning("email.send_failed scope=%s id=%s err=%s", scope, row_id, exc)
            return row.status
        row.status, row.provider_message_id, row.sent_at, row.error = "sent", pid, datetime.utcnow(), None
        db.commit()
        return "sent"
    finally:
        db.close()


def sweep_due(limit: int = 200, bind=None) -> int:
    """Send rows whose send_after has passed; recover rows stuck in 'sending'."""
    sent = 0
    db = _session(bind)
    try:
        now = datetime.utcnow()
        for scope, cfg in SCOPES.items():
            m = cfg.model
            # A worker that died mid-send leaves 'sending' rows; retry after 10 min.
            db.query(m).filter(m.status == "sending", m.updated_at < now - timedelta(minutes=10)).update(
                {"status": "queued"}, synchronize_session=False
            )
            db.commit()
            ids = [r[0] for r in db.query(m.id).filter(m.status == "queued", m.send_after <= now)
                   .order_by(m.send_after).limit(limit).all()]
            for i in ids:
                if send_one(scope, i, bind=bind) == "sent":
                    sent += 1
    finally:
        db.close()
    return sent

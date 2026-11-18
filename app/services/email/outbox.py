"""
Outbox: render + persist an email row in the caller's transaction.

Nothing is sent here. The row is marked for dispatch and handed to the sender
after the transaction commits (see dispatch.py), so a rolled-back change never
emails anyone and a provider outage never loses an email.

Scope-agnostic: consumer rows go to email_messages, recruiter rows to
rec_email_messages. The caller passes the model and owner columns; this module
never joins across the two.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.email import OpsEmailSuppression
from app.services.email import dispatch
from app.services.email.render import render
from app.services.email.templates import resolve
from app.services.email.themes import Theme
from app.services.email.tokens import make_unsub_token

logger = logging.getLogger(__name__)

# Which preference toggle governs each category (transactional has none).
CATEGORY_PREF = {"notification": "notifications", "digest": "digests", "lifecycle": "product_updates"}


@dataclass
class QueueResult:
    row: Any | None
    status: str  # queued|duplicate|suppressed|capped|missing_template|inactive_template


def normalise(email: str | None) -> str:
    return (email or "").strip().lower()


def is_suppressed(db: Session, email: str) -> bool:
    return db.query(OpsEmailSuppression.id).filter(OpsEmailSuppression.email == normalise(email)).first() is not None


def unsubscribe_links(scope: str, subject_id: int, category: str, app_url: str) -> tuple[str, str | None]:
    """(browser link, one-click POST url or None)."""
    tok = make_unsub_token(scope, subject_id, category)
    page = f"{app_url.rstrip('/')}/unsubscribe?token={tok}"
    one_click = f"{settings.api_public_url.rstrip('/')}/api/v1/email/unsubscribe?token={tok}" if settings.api_public_url else None
    return page, one_click


def queue_email(
    db: Session,
    *,
    model: type,
    scope: str,
    owner: dict[str, Any],
    subject_id: int | None,
    to_email: str,
    template_key: str,
    ctx: dict[str, Any],
    theme: Theme,
    idempotency_key: str,
    event_key: str | None = None,
    rule_id: int | None = None,
    delay_minutes: int = 0,
    from_addr: str | None = None,
    reply_to: str | None = None,
    footer_reason: str | None = None,
    prefs_path: str = "/settings",
    cap_filter: list | None = None,
) -> QueueResult:
    to_email = normalise(to_email)
    if not to_email or "@" not in to_email:
        return QueueResult(None, "invalid_address")

    if db.query(model.id).filter(model.idempotency_key == idempotency_key[:200]).first():
        return QueueResult(None, "duplicate")

    spec = resolve(db, template_key)
    if spec is None:
        logger.warning("email.missing_template key=%s", template_key)
        return QueueResult(None, "missing_template")
    if not spec.is_active:
        return QueueResult(None, "inactive_template")

    status, error = "queued", None
    if is_suppressed(db, to_email):
        status, error = "suppressed", "address is on the suppression list"
    elif spec.category == "lifecycle" and cap_filter is not None and settings.email_lifecycle_weekly_cap > 0:
        since = datetime.utcnow() - timedelta(days=7)
        recent = (
            db.query(model.id)
            .filter(*cap_filter, model.category == "lifecycle", model.created_at >= since,
                    model.status.in_(("queued", "sending", "sent", "delivered")))
            .count()
        )
        if recent >= settings.email_lifecycle_weekly_cap:
            # No row: a capped once-per-person rule should get another chance next week.
            logger.info("email.lifecycle_capped to=%s template=%s", to_email, template_key)
            return QueueResult(None, "capped")

    unsub_page = one_click = None
    if spec.category != "transactional" and subject_id is not None:
        unsub_page, one_click = unsubscribe_links(scope, subject_id, spec.category, theme.app_url or settings.consumer_app_url)

    full_ctx = {"app_url": theme.app_url, **ctx}
    rendered = render(
        theme=theme,
        subject=spec.subject,
        body=spec.body,
        ctx=full_ctx,
        preheader=spec.preheader,
        heading=spec.heading,
        cta_label=spec.cta_label,
        cta_url=spec.cta_url,
        blocks=spec.blocks,
        footer_reason=footer_reason,
        prefs_url=(theme.app_url.rstrip("/") + prefs_path) if (theme.app_url and subject_id is not None) else None,
        unsubscribe_url=unsub_page,
    )

    meta = {"_from": from_addr, "_reply_to": reply_to, "_unsub_page": unsub_page, "_unsub_one_click": one_click}
    stored_ctx = {k: v for k, v in {**ctx, **meta}.items() if _jsonable(v)}

    row = model(
        **owner,
        to_email=to_email,
        template_key=template_key,
        category=spec.category,
        subject=rendered.subject,
        html=rendered.html,
        text=rendered.text,
        context=stored_ctx,
        status=status,
        error=error,
        event_key=event_key,
        rule_id=rule_id,
        idempotency_key=idempotency_key[:200],
        send_after=datetime.utcnow() + timedelta(minutes=max(0, delay_minutes)),
    )
    if hasattr(model, "reply_to") and reply_to:
        row.reply_to = reply_to
    db.add(row)
    db.flush()
    if status == "queued":
        dispatch.mark_for_dispatch(db, scope, row.id, delayed=delay_minutes > 0)
    return QueueResult(row, status)


def _jsonable(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool, list, dict))

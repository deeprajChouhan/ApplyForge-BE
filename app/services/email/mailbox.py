"""
Operator mailbox: support@ and info@ (admin-only).

Inbound: Resend `email.received` webhook → fetch body + attachments from the
Receiving API (the webhook is metadata only) → thread → store.
Outbound: replies/compose rendered in the plain ops theme and sent through
the same EmailProvider, so they thread normally in the customer's client.

Mail to support@ from a known consumer user is mirrored into the existing
help desk (SupportTicket) so there's one conversation, two surfaces. Recruiter
senders are only *looked up* for context (in the API layer, per product) —
never linked by FK.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta
from email.utils import parseaddr
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.email import OpsMailboxMessage, OpsMailboxThread
from app.models.models import SupportTicket, SupportTicketMessage, User
from app.services.email.provider import EmailSendError, OutgoingEmail, get_email_provider
from app.services.email.render import html_to_text, render
from app.services.email.themes import OPS

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
BLOCKED_EXT = (".exe", ".bat", ".cmd", ".com", ".scr", ".js", ".vbs", ".msi", ".jar", ".ps1", ".sh", ".dll")
_RE_PREFIX = re.compile(r"^\s*((re|fw|fwd|aw)\s*:\s*)+", re.I)
_TICKET_TAG = re.compile(r"\[#(\d+)\]")


def mailboxes() -> dict[str, str]:
    """{'support': 'support@applyforge.co.uk', 'info': 'info@…'}"""
    out = {}
    for addr in (a.strip().lower() for a in settings.email_mailboxes.split(",") if a.strip()):
        out[addr.split("@")[0]] = addr
    return out


def mailbox_for(addresses: list[str]) -> str | None:
    boxes = {v: k for k, v in mailboxes().items()}
    for a in addresses or []:
        _, addr = parseaddr(a)
        if addr.lower() in boxes:
            return boxes[addr.lower()]
    # Anything else at our domain (catch-all) lands in info@.
    for a in addresses or []:
        _, addr = parseaddr(a)
        if addr.lower().endswith("@" + settings.email_domain.lower()):
            return "info" if "info" in mailboxes() else next(iter(mailboxes()), None)
    return None


def norm_subject(s: str | None) -> str:
    return _RE_PREFIX.sub("", s or "").strip().lower()


def _snippet(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:280]


def _find_thread(db: Session, mailbox: str, sender: str, subject: str, in_reply_to: str | None,
                 references: str | None) -> OpsMailboxThread | None:
    ids = [i for i in ([in_reply_to] + (references or "").split()) if i]
    if ids:
        m = db.query(OpsMailboxMessage).filter(OpsMailboxMessage.message_id.in_(ids)).first()
        if m:
            return db.get(OpsMailboxThread, m.thread_id)
    ns = norm_subject(subject)
    since = datetime.utcnow() - timedelta(days=30)
    for t in (db.query(OpsMailboxThread)
              .filter(OpsMailboxThread.mailbox == mailbox, OpsMailboxThread.counterpart_email == sender,
                      OpsMailboxThread.last_message_at >= since)
              .order_by(OpsMailboxThread.last_message_at.desc()).limit(20)):
        if norm_subject(t.subject) == ns:
            return t
    return None


def _store_attachments(provider, email_id: str, thread_id: int, meta: list[dict]) -> list[dict]:
    if not meta:
        return []
    from app.services.storage.service import get_storage_service

    storage = get_storage_service()
    out = []
    try:
        listed = {a["id"]: a for a in provider.list_received_attachments(email_id)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("mailbox.attachments_list_failed id=%s err=%s", email_id, exc)
        listed = {}
    for a in meta:
        info = {**a, **listed.get(a.get("id"), {})}
        name = info.get("filename") or "attachment"
        entry = {"filename": name, "content_type": info.get("content_type"), "size": info.get("size"),
                 "content_id": info.get("content_id"), "inline": info.get("content_disposition") == "inline"}
        if name.lower().endswith(BLOCKED_EXT):
            entry["blocked"] = "file type not allowed"
        elif (info.get("size") or 0) > MAX_ATTACHMENT_BYTES:
            entry["blocked"] = "too large"
        elif info.get("download_url"):
            try:
                data = provider.download(info["download_url"])
                entry["storage_key"] = storage.upload_bytes(content=data, key_prefix=f"mailbox/{thread_id}",
                                                            filename=name, content_type=info.get("content_type"))
            except Exception as exc:  # noqa: BLE001
                entry["blocked"] = "download failed"
                logger.warning("mailbox.attachment_failed id=%s err=%s", email_id, exc)
        out.append(entry)
    return out


def ingest_received(db: Session, data: dict) -> OpsMailboxMessage | None:
    """Handle an `email.received` webhook payload (`data` object)."""
    to = list(data.get("to") or []) + list(data.get("cc") or []) + list(data.get("received_for") or [])
    box = mailbox_for(to)
    if box is None:
        logger.info("mailbox.ignored to=%s", to)
        return None
    email_id = data.get("email_id")
    if email_id and db.query(OpsMailboxMessage.id).filter(OpsMailboxMessage.resend_email_id == email_id).first():
        return None  # already ingested

    provider = get_email_provider()
    full: dict[str, Any] = {}
    if email_id:
        try:
            full = provider.get_received(email_id) or {}
        except Exception as exc:  # noqa: BLE001 — keep metadata; body can be refetched
            logger.warning("mailbox.fetch_failed id=%s err=%s", email_id, exc)
    headers = {str(k).lower(): v for k, v in (full.get("headers") or {}).items()}
    from_name, from_addr = parseaddr(headers.get("from") or full.get("from") or data.get("from") or "")
    from_addr = (from_addr or data.get("from") or "").lower()
    subject = full.get("subject") or data.get("subject") or "(no subject)"
    html = full.get("html")
    text = full.get("text") or (html_to_text(html) if html else None)
    in_reply_to = headers.get("in-reply-to")
    references = headers.get("references")
    message_id = full.get("message_id") or data.get("message_id")

    thread = _find_thread(db, box, from_addr, subject, in_reply_to, references)
    if thread is None:
        thread = OpsMailboxThread(mailbox=box, subject=subject[:300], counterpart_email=from_addr,
                                  counterpart_name=from_name or None, status="open", unread=True, message_count=0)
        db.add(thread)
        db.flush()
    msg = OpsMailboxMessage(
        thread_id=thread.id, direction="inbound", from_email=from_addr, from_name=from_name or None,
        to=list(data.get("to") or []), cc=list(data.get("cc") or []), subject=subject[:300],
        html=html, text=text, message_id=message_id, in_reply_to=in_reply_to, references=references,
        resend_email_id=email_id, status="received", authentication=full.get("authentication"),
    )
    db.add(msg)
    db.flush()
    msg.attachments = _store_attachments(provider, email_id, thread.id, full.get("attachments") or data.get("attachments") or []) if email_id else []
    thread.message_count = (thread.message_count or 0) + 1
    thread.last_message_at = datetime.utcnow()
    thread.unread = True
    thread.snippet = _snippet(text)
    if thread.status in ("closed", "waiting", "archived"):
        thread.status = "open"
    if box == "support":
        _mirror_to_helpdesk(db, thread, from_addr, subject, text or "")
    return msg


def _mirror_to_helpdesk(db: Session, thread: OpsMailboxThread, sender: str, subject: str, body: str) -> None:
    """Consumer user wrote to support@ → same conversation in their help-desk ticket."""
    user = db.query(User).filter(User.email == sender).first()
    if user is None:
        return
    ticket = None
    m = _TICKET_TAG.search(subject or "")
    if m:
        t = db.get(SupportTicket, int(m.group(1)))
        ticket = t if t and t.user_id == user.id else None
    if ticket is None and thread.support_ticket_id:
        t = db.get(SupportTicket, thread.support_ticket_id)
        ticket = t if t and t.user_id == user.id else None
    if ticket is None:
        ticket = SupportTicket(user_id=user.id, subject=norm_subject(subject).capitalize()[:255] or "Email to support",
                               category="email", priority="normal", status="open")
        db.add(ticket)
        db.flush()
    elif ticket.status not in ("open", "in_progress"):
        ticket.status, ticket.resolved_at, ticket.closed_at = "open", None, None
    db.add(SupportTicketMessage(ticket_id=ticket.id, sender_user_id=user.id, sender_role="user", body=body[:20000] or "(empty email)"))
    thread.support_ticket_id = ticket.id


def _from_for(box: str) -> str:
    addr = mailboxes().get(box) or f"{box}@{settings.email_domain}"
    label = "ApplyForge Support" if box == "support" else "ApplyForge"
    return f"{label} <{addr}>"


def _signature(box: str) -> str:
    return settings.email_signature_support if box == "support" else settings.email_signature_info


def send_outbound(db: Session, thread: OpsMailboxThread, *, admin_id: int, body: str, to: list[str] | None = None,
                  cc: list[str] | None = None, subject: str | None = None) -> OpsMailboxMessage:
    last_in = (db.query(OpsMailboxMessage).filter(OpsMailboxMessage.thread_id == thread.id)
               .order_by(OpsMailboxMessage.created_at.desc()).first())
    if subject:
        subj = subject
    else:
        base = thread.subject or ""
        subj = base if base.lower().startswith("re:") else f"Re: {base}"
    if thread.support_ticket_id and not _TICKET_TAG.search(subj):
        subj = f"{subj} [#{thread.support_ticket_id}]"
    rendered = render(theme=OPS, subject=subj, body=body, ctx={}, signature=_signature(thread.mailbox), plain=True)
    our_id = f"<af-{uuid.uuid4().hex}@{settings.email_domain}>"
    headers = {"Message-ID": our_id}
    refs = None
    if last_in and last_in.message_id:
        headers["In-Reply-To"] = last_in.message_id
        refs = " ".join(x for x in [last_in.references, last_in.message_id] if x)
        headers["References"] = refs
    recipients = to or [thread.counterpart_email]
    msg = OpsMailboxMessage(
        thread_id=thread.id, direction="outbound", from_email=mailboxes().get(thread.mailbox, ""),
        from_name="ApplyForge", to=recipients, cc=cc or [], subject=rendered.subject, html=rendered.html,
        text=rendered.text, message_id=our_id, in_reply_to=headers.get("In-Reply-To"), references=refs,
        status="queued", sent_by_admin_id=admin_id,
    )
    db.add(msg)
    db.flush()
    try:
        msg.resend_email_id = get_email_provider().send(OutgoingEmail(
            from_addr=_from_for(thread.mailbox), to=recipients, cc=cc or None, subject=rendered.subject,
            html=rendered.html, text=rendered.text, headers=headers,
            tags={"scope": "ops", "mailbox": thread.mailbox}, idempotency_key=f"ops-{msg.id}",
        ))
        msg.status = "sent"
    except EmailSendError as exc:
        msg.status, msg.error = "failed", str(exc)[:2000]
    thread.message_count = (thread.message_count or 0) + 1
    thread.last_message_at = datetime.utcnow()
    thread.snippet = _snippet(rendered.text)
    thread.unread = False
    if msg.status == "sent" and thread.status == "open":
        thread.status = "waiting"
    if msg.status == "sent" and thread.support_ticket_id:
        ticket = db.get(SupportTicket, thread.support_ticket_id)
        if ticket is not None:
            tm = SupportTicketMessage(ticket_id=ticket.id, sender_user_id=admin_id, sender_role="admin", body=body)
            tm._email_suppress = True  # already emailed from the mailbox — don't send c_support_reply too
            db.add(tm)
            if ticket.status == "open":
                ticket.status = "in_progress"
    return msg


def compose(db: Session, *, admin_id: int, mailbox: str, to: str, subject: str, body: str,
            cc: list[str] | None = None) -> OpsMailboxThread:
    name, addr = parseaddr(to)
    thread = OpsMailboxThread(mailbox=mailbox, subject=subject[:300], counterpart_email=addr.lower(),
                              counterpart_name=name or None, status="waiting", unread=False, message_count=0)
    db.add(thread)
    db.flush()
    send_outbound(db, thread, admin_id=admin_id, body=body, to=[addr.lower()], cc=cc, subject=subject)
    return thread

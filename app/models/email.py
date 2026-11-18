"""
Email tables.

Consumer side (user_id FK, cascade):
  email_messages       — outbox + send log for mail to consumer users
  email_preferences    — per-user opt-outs (soft opt-in: everything on by default)

Operator side (ops_*, no FKs into either product — the data wall stays intact):
  ops_email_templates  — admin overrides of system templates + custom templates
  ops_email_rules      — admin-built automations ("when X, if Y, send Z")
  ops_email_suppressions — addresses we must not mail (bounce / complaint / manual)
  ops_email_webhook_events — Resend webhook de-duplication (svix-id)
  ops_mailbox_threads / ops_mailbox_messages / ops_canned_replies — support@ / info@

Recruiter-side outbox + preferences live in app/recruiter/models.py (rec_*).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Email bodies (esp. inbound HTML) can exceed MySQL TEXT's 64 KB.
LongText = Text().with_variant(LONGTEXT(), "mysql")


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    to_email: Mapped[str] = mapped_column(String(320), nullable=False)
    template_key: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # transactional|notification|lifecycle
    subject: Mapped[str] = mapped_column(String(300), default="")
    html: Mapped[str | None] = mapped_column(LongText)
    text: Mapped[str | None] = mapped_column(LongText)
    context: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    event_key: Mapped[str | None] = mapped_column(String(80), index=True)
    rule_id: Mapped[int | None] = mapped_column(Integer, index=True)  # ops_email_rules.id (no FK)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(100), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    send_after: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime)
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailPreference(Base):
    __tablename__ = "email_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    notifications: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    digests: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    product_updates: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── Operator tables ────────────────────────────────────────────────────────


class OpsEmailTemplate(Base):
    """A DB row overrides the code default of the same key; non-system rows are custom templates."""

    __tablename__ = "ops_email_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    product: Mapped[str] = mapped_column(String(20), nullable=False)  # consumer|recruiter|ops
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    preheader: Mapped[str | None] = mapped_column(String(300))
    heading: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    cta_label: Mapped[str | None] = mapped_column(String(120))
    cta_url: Mapped[str | None] = mapped_column(String(500))
    blocks: Mapped[list | None] = mapped_column(JSON)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OpsEmailRule(Base):
    __tablename__ = "ops_email_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(20), index=True, nullable=False)  # consumer|recruiter
    event_key: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    conditions: Mapped[dict | None] = mapped_column(JSON)  # {"match": "all"|"any", "rules": [{field, op, value}]}
    template_key: Mapped[str] = mapped_column(String(80), nullable=False)
    recipient: Mapped[str] = mapped_column(String(30), default="subject")  # subject|agency_owner|agency_all|fixed
    fixed_to: Mapped[str | None] = mapped_column(String(320))
    delay_minutes: Mapped[int] = mapped_column(Integer, default=0)
    cancel_if: Mapped[dict | None] = mapped_column(JSON)
    cooldown_days: Mapped[int] = mapped_column(Integer, default=0)  # 0 = no cooldown
    once_per_subject: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int | None] = mapped_column(Integer)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime)
    fire_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OpsEmailSuppression(Base):
    __tablename__ = "ops_email_suppressions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(30), nullable=False)  # bounced|complained|manual
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OpsEmailWebhookEvent(Base):
    __tablename__ = "ops_email_webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    svix_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class OpsMailboxThread(Base):
    __tablename__ = "ops_mailbox_threads"
    __table_args__ = (Index("ix_ops_mailbox_threads_box_last", "mailbox", "last_message_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mailbox: Mapped[str] = mapped_column(String(30), index=True, nullable=False)  # support|info
    subject: Mapped[str] = mapped_column(String(300), default="")
    counterpart_email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    counterpart_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open|waiting|closed|spam|archived
    starred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unread: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    labels: Mapped[list | None] = mapped_column(JSON)
    support_ticket_id: Mapped[int | None] = mapped_column(Integer, index=True)  # consumer ticket, resolved in UI
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    snippet: Mapped[str | None] = mapped_column(String(300))
    last_message_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OpsMailboxMessage(Base):
    __tablename__ = "ops_mailbox_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("ops_mailbox_threads.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # inbound|outbound
    from_email: Mapped[str] = mapped_column(String(320), nullable=False)
    from_name: Mapped[str | None] = mapped_column(String(200))
    to: Mapped[list | None] = mapped_column(JSON)
    cc: Mapped[list | None] = mapped_column(JSON)
    subject: Mapped[str] = mapped_column(String(300), default="")
    html: Mapped[str | None] = mapped_column(LongText)
    text: Mapped[str | None] = mapped_column(LongText)
    message_id: Mapped[str | None] = mapped_column(String(300), index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(300))
    references: Mapped[str | None] = mapped_column(Text)
    resend_email_id: Mapped[str | None] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(20), default="received")  # received|queued|sent|delivered|bounced|failed
    attachments: Mapped[list | None] = mapped_column(JSON)  # [{filename, content_type, size, storage_key}]
    authentication: Mapped[dict | None] = mapped_column(JSON)  # spf/dkim/dmarc
    sent_by_admin_id: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class OpsCannedReply(Base):
    __tablename__ = "ops_canned_replies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    mailbox: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

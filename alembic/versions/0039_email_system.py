"""email system (Resend)

Consumer (user_id FK, cascade):
- email_messages, email_preferences
Recruiter (agency_id FK, cascade — inside the rec_* wall):
- rec_email_messages, rec_email_preferences
Operator (no FKs into either product):
- ops_email_templates, ops_email_rules, ops_email_suppressions, ops_email_webhook_events,
  ops_mailbox_threads, ops_mailbox_messages, ops_canned_replies

Idempotent: each table is skipped if it already exists (rec_* are also created
at boot by ensure_recruiter_tables).

Revision ID: 0039_email_system
Revises: 0038_recruiter_enquiries
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0039_email_system"
down_revision = "0038_recruiter_enquiries"
branch_labels = None
depends_on = None

LONG = sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def _log_columns() -> list:
    return [
        sa.Column("to_email", sa.String(320), nullable=False),
        sa.Column("template_key", sa.String(80), nullable=False, index=True),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(300), server_default=""),
        sa.Column("html", LONG),
        sa.Column("text", LONG),
        sa.Column("context", sa.JSON),
        sa.Column("status", sa.String(20), server_default="queued", index=True),
        sa.Column("event_key", sa.String(80), index=True),
        sa.Column("rule_id", sa.Integer, index=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column("provider_message_id", sa.String(100), index=True),
        sa.Column("error", sa.Text),
        sa.Column("attempts", sa.Integer, server_default="0"),
        sa.Column("send_after", sa.DateTime, server_default=sa.func.now(), index=True),
        sa.Column("sent_at", sa.DateTime),
        sa.Column("delivered_at", sa.DateTime),
        sa.Column("opened_at", sa.DateTime),
        sa.Column("clicked_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), index=True),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    has = insp.has_table

    if not has("email_messages"):
        op.create_table(
            "email_messages",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            *_log_columns(),
        )
    if not has("email_preferences"):
        op.create_table(
            "email_preferences",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True),
            sa.Column("notifications", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("digests", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("product_updates", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        )
    if not has("rec_email_messages"):
        op.create_table(
            "rec_email_messages",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("agency_id", sa.Integer, sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("recruiter_id", sa.Integer, sa.ForeignKey("rec_recruiters.id", ondelete="SET NULL"), index=True),
            sa.Column("is_external", sa.Boolean, nullable=False, server_default="0"),
            sa.Column("reply_to", sa.String(320)),
            *_log_columns(),
        )
    if not has("rec_email_preferences"):
        op.create_table(
            "rec_email_preferences",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("agency_id", sa.Integer, sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("recruiter_id", sa.Integer, sa.ForeignKey("rec_recruiters.id", ondelete="CASCADE"), nullable=False, unique=True, index=True),
            sa.Column("notifications", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("digests", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("product_updates", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("muted_kinds", sa.JSON),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        )
    if not has("ops_email_templates"):
        op.create_table(
            "ops_email_templates",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("key", sa.String(80), nullable=False, unique=True),
            sa.Column("product", sa.String(20), nullable=False),
            sa.Column("category", sa.String(20), nullable=False),
            sa.Column("name", sa.String(160), nullable=False),
            sa.Column("subject", sa.String(300), nullable=False),
            sa.Column("preheader", sa.String(300)),
            sa.Column("heading", sa.String(300)),
            sa.Column("body", sa.Text, nullable=False),
            sa.Column("cta_label", sa.String(120)),
            sa.Column("cta_url", sa.String(500)),
            sa.Column("blocks", sa.JSON),
            sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("updated_by", sa.Integer),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        )
    if not has("ops_email_rules"):
        op.create_table(
            "ops_email_rules",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(160), nullable=False),
            sa.Column("description", sa.Text),
            sa.Column("scope", sa.String(20), nullable=False, index=True),
            sa.Column("event_key", sa.String(80), nullable=False, index=True),
            sa.Column("conditions", sa.JSON),
            sa.Column("template_key", sa.String(80), nullable=False),
            sa.Column("recipient", sa.String(30), server_default="subject"),
            sa.Column("fixed_to", sa.String(320)),
            sa.Column("delay_minutes", sa.Integer, server_default="0"),
            sa.Column("cancel_if", sa.JSON),
            sa.Column("cooldown_days", sa.Integer, server_default="0"),
            sa.Column("once_per_subject", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("version", sa.Integer, server_default="1"),
            sa.Column("created_by", sa.Integer),
            sa.Column("last_fired_at", sa.DateTime),
            sa.Column("fire_count", sa.Integer, server_default="0"),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        )
    if not has("ops_email_suppressions"):
        op.create_table(
            "ops_email_suppressions",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("email", sa.String(320), nullable=False, unique=True),
            sa.Column("reason", sa.String(30), nullable=False),
            sa.Column("note", sa.String(300)),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        )
    if not has("ops_email_webhook_events"):
        op.create_table(
            "ops_email_webhook_events",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("svix_id", sa.String(100), nullable=False, unique=True),
            sa.Column("event_type", sa.String(60), index=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), index=True),
        )
    if not has("ops_mailbox_threads"):
        op.create_table(
            "ops_mailbox_threads",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("mailbox", sa.String(30), nullable=False, index=True),
            sa.Column("subject", sa.String(300), server_default=""),
            sa.Column("counterpart_email", sa.String(320), nullable=False, index=True),
            sa.Column("counterpart_name", sa.String(200)),
            sa.Column("status", sa.String(20), server_default="open", index=True),
            sa.Column("starred", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("unread", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("labels", sa.JSON),
            sa.Column("support_ticket_id", sa.Integer, index=True),
            sa.Column("message_count", sa.Integer, server_default="0"),
            sa.Column("snippet", sa.String(300)),
            sa.Column("last_message_at", sa.DateTime, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        )
        op.create_index("ix_ops_mailbox_threads_box_last", "ops_mailbox_threads", ["mailbox", "last_message_at"])
    if not has("ops_mailbox_messages"):
        op.create_table(
            "ops_mailbox_messages",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("thread_id", sa.Integer, sa.ForeignKey("ops_mailbox_threads.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("direction", sa.String(10), nullable=False),
            sa.Column("from_email", sa.String(320), nullable=False),
            sa.Column("from_name", sa.String(200)),
            sa.Column("to", sa.JSON),
            sa.Column("cc", sa.JSON),
            sa.Column("subject", sa.String(300), server_default=""),
            sa.Column("html", LONG),
            sa.Column("text", LONG),
            sa.Column("message_id", sa.String(300), index=True),
            sa.Column("in_reply_to", sa.String(300)),
            sa.Column("references", sa.Text),
            sa.Column("resend_email_id", sa.String(100), index=True),
            sa.Column("status", sa.String(20), server_default="received"),
            sa.Column("attachments", sa.JSON),
            sa.Column("authentication", sa.JSON),
            sa.Column("sent_by_admin_id", sa.Integer),
            sa.Column("error", sa.Text),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), index=True),
        )
    if not has("ops_canned_replies"):
        op.create_table(
            "ops_canned_replies",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("title", sa.String(160), nullable=False),
            sa.Column("body", sa.Text, nullable=False),
            sa.Column("mailbox", sa.String(30)),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for t in ("ops_canned_replies", "ops_mailbox_messages", "ops_mailbox_threads", "ops_email_webhook_events",
              "ops_email_suppressions", "ops_email_rules", "ops_email_templates", "rec_email_preferences",
              "rec_email_messages", "email_preferences", "email_messages"):
        if insp.has_table(t):
            op.drop_table(t)

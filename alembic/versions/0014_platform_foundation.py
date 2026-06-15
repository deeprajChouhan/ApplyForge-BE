"""platform foundation: audit, analytics, plans, support, interview, soft deletes

Adds:
  - audit_logs                (append-only admin/user action trail)
  - product_events            (first-party product + website analytics)
  - plans                     (admin-managed pricing plans, seeded free/pro/enterprise)
  - support_tickets / support_ticket_messages (help desk)
  - interview_sessions / interview_answers    (mock interview practice)
  - user_profiles.onboarding_completed
  - soft-delete columns on job_applications, generated_documents,
    parsed_resume_data, knowledge_documents, crawled_jobs
  - usage_ledger.packages_used
  - job_discovery / interview_practice / package_generation feature flags

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-15
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_ALL_FLAGS = (
    "'jd_analyze','applications','kanban','resume','chat','multi_resume','job_crawler',"
    "'job_discovery','interview_practice','package_generation'"
)
_OLD_FLAGS = "'jd_analyze','applications','kanban','resume','chat','multi_resume','job_crawler'"


def upgrade() -> None:
    # ── audit_logs ──────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_role", sa.String(20), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("before", sa.Text(), nullable=True),
        sa.Column("after", sa.Text(), nullable=True),
        sa.Column("extra_metadata", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_entity_type", "audit_logs", ["entity_type"])
    op.create_index("ix_audit_logs_entity_id", "audit_logs", ["entity_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_actor_created", "audit_logs", ["actor_user_id", "created_at"])
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])

    # ── product_events ──────────────────────────────────────────────────────
    op.create_table(
        "product_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_name", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("properties", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("referrer", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_product_events_user_id", "product_events", ["user_id"])
    op.create_index("ix_product_events_event_name", "product_events", ["event_name"])
    op.create_index("ix_product_events_created_at", "product_events", ["created_at"])
    op.create_index("ix_product_events_name_created", "product_events", ["event_name", "created_at"])
    op.create_index("ix_product_events_user_created", "product_events", ["user_id", "created_at"])

    # ── plans ───────────────────────────────────────────────────────────────
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_monthly", sa.Float(), nullable=False, server_default="0"),
        sa.Column("price_yearly", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(10), nullable=False, server_default="usd"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("features", sa.Text(), nullable=True),
        sa.Column("limits", sa.Text(), nullable=True),
        sa.Column("cta_label", sa.String(50), nullable=True),
        sa.Column("highlighted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name="uq_plans_slug"),
    )
    op.create_index("ix_plans_slug", "plans", ["slug"])
    op.create_index("ix_plans_deleted_at", "plans", ["deleted_at"])

    # ── support_tickets ─────────────────────────────────────────────────────
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("category", sa.String(50), nullable=False, server_default="general"),
        sa.Column("priority", sa.String(20), nullable=False, server_default="normal"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("related_entity_type", sa.String(50), nullable=True),
        sa.Column("related_entity_id", sa.Integer(), nullable=True),
        sa.Column("assigned_admin_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_support_tickets_user_id", "support_tickets", ["user_id"])
    op.create_index("ix_support_tickets_status", "support_tickets", ["status"])
    op.create_index("ix_support_tickets_assigned_admin_id", "support_tickets", ["assigned_admin_id"])
    op.create_index("ix_support_tickets_deleted_at", "support_tickets", ["deleted_at"])

    # ── support_ticket_messages ─────────────────────────────────────────────
    op.create_table(
        "support_ticket_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_role", sa.String(20), nullable=False, server_default="user"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_support_ticket_messages_ticket_id", "support_ticket_messages", ["ticket_id"])

    # ── interview_sessions ──────────────────────────────────────────────────
    op.create_table(
        "interview_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("job_applications.id", ondelete="SET NULL"), nullable=True),
        sa.Column("role_title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="in_progress"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("delete_reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_interview_sessions_user_id", "interview_sessions", ["user_id"])
    op.create_index("ix_interview_sessions_application_id", "interview_sessions", ["application_id"])
    op.create_index("ix_interview_sessions_status", "interview_sessions", ["status"])
    op.create_index("ix_interview_sessions_deleted_at", "interview_sessions", ["deleted_at"])

    # ── interview_answers ───────────────────────────────────────────────────
    op.create_table(
        "interview_answers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_index", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_interview_answers_session_id", "interview_answers", ["session_id"])
    op.create_index("ix_interview_answers_session_q", "interview_answers", ["session_id", "question_index"])

    # ── user_profiles.onboarding_completed ──────────────────────────────────
    op.add_column(
        "user_profiles",
        sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default="0"),
    )

    # ── soft-delete columns ─────────────────────────────────────────────────
    op.add_column("job_applications", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("job_applications", sa.Column("deleted_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("job_applications", sa.Column("delete_reason", sa.String(500), nullable=True))
    op.create_index("ix_job_applications_deleted_at", "job_applications", ["deleted_at"])

    op.add_column("generated_documents", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_generated_documents_deleted_at", "generated_documents", ["deleted_at"])

    op.add_column("parsed_resume_data", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("parsed_resume_data", sa.Column("deleted_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_parsed_resume_data_deleted_at", "parsed_resume_data", ["deleted_at"])

    op.add_column("knowledge_documents", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_knowledge_documents_deleted_at", "knowledge_documents", ["deleted_at"])

    op.add_column("crawled_jobs", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_crawled_jobs_deleted_at", "crawled_jobs", ["deleted_at"])

    # ── usage_ledger.packages_used ──────────────────────────────────────────
    op.add_column("usage_ledger", sa.Column("packages_used", sa.Integer(), nullable=False, server_default="0"))

    # ── extend FeatureFlag enum ──────────────────────────────────────────────
    op.execute(f"ALTER TABLE user_features MODIFY COLUMN feature ENUM({_ALL_FLAGS}) NOT NULL")
    op.execute(f"ALTER TABLE usage_events MODIFY COLUMN feature ENUM({_ALL_FLAGS}) NULL")

    # ── seed default plans ──────────────────────────────────────────────────
    plans_table = sa.table(
        "plans",
        sa.column("name", sa.String),
        sa.column("slug", sa.String),
        sa.column("description", sa.Text),
        sa.column("price_monthly", sa.Float),
        sa.column("price_yearly", sa.Float),
        sa.column("currency", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("is_public", sa.Boolean),
        sa.column("sort_order", sa.Integer),
        sa.column("features", sa.Text),
        sa.column("limits", sa.Text),
        sa.column("cta_label", sa.String),
        sa.column("highlighted", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    now = datetime.utcnow()
    op.bulk_insert(
        plans_table,
        [
            {
                "name": "Free",
                "slug": "free",
                "description": "Explore ApplyForge with no commitment.",
                "price_monthly": 0,
                "price_yearly": 0,
                "currency": "usd",
                "is_active": True,
                "is_public": True,
                "sort_order": 0,
                "features": (
                    '["5 application packages per month",'
                    ' "AI-generated resumes, cover letters & cold emails",'
                    ' "Job description analysis & match scoring",'
                    ' "Application tracking"]'
                ),
                "limits": (
                    '{"applications": -1, "packages_per_month": 5, "job_discovery": false,'
                    ' "kanban": false, "mock_interview_sessions": 0, "ai_chat": false,'
                    ' "resume_uploads": 1}'
                ),
                "cta_label": "Start applying smarter",
                "highlighted": False,
                "created_at": now,
                "updated_at": now,
            },
            {
                "name": "Pro",
                "slug": "pro",
                "description": "For job seekers applying regularly.",
                "price_monthly": 19,
                "price_yearly": 180,
                "currency": "usd",
                "is_active": True,
                "is_public": True,
                "sort_order": 1,
                "features": (
                    '["Unlimited application packages",'
                    ' "Job discovery feed",'
                    ' "AI mock interview practice",'
                    ' "Full application pipeline & Kanban board",'
                    ' "AI chat coach for every application",'
                    ' "Multiple resumes & per-application tailoring",'
                    ' "Automated job crawler"]'
                ),
                "limits": (
                    '{"applications": -1, "packages_per_month": -1, "job_discovery": true,'
                    ' "kanban": true, "mock_interview_sessions": 20, "ai_chat": true,'
                    ' "resume_uploads": -1}'
                ),
                "cta_label": "Start Pro",
                "highlighted": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "name": "Enterprise",
                "slug": "enterprise",
                "description": "For teams and power users.",
                "price_monthly": 49,
                "price_yearly": 470,
                "currency": "usd",
                "is_active": True,
                "is_public": True,
                "sort_order": 2,
                "features": (
                    '["Everything in Pro",'
                    ' "Unlimited mock interview sessions",'
                    ' "Priority processing",'
                    ' "Dedicated support"]'
                ),
                "limits": (
                    '{"applications": -1, "packages_per_month": -1, "job_discovery": true,'
                    ' "kanban": true, "mock_interview_sessions": -1, "ai_chat": true,'
                    ' "resume_uploads": -1}'
                ),
                "cta_label": "Contact sales",
                "highlighted": False,
                "created_at": now,
                "updated_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE usage_events MODIFY COLUMN feature ENUM({_OLD_FLAGS}) NULL")
    op.execute(f"ALTER TABLE user_features MODIFY COLUMN feature ENUM({_OLD_FLAGS}) NOT NULL")

    op.drop_column("usage_ledger", "packages_used")

    op.drop_index("ix_crawled_jobs_deleted_at", "crawled_jobs")
    op.drop_column("crawled_jobs", "deleted_at")

    op.drop_index("ix_knowledge_documents_deleted_at", "knowledge_documents")
    op.drop_column("knowledge_documents", "deleted_at")

    op.drop_index("ix_parsed_resume_data_deleted_at", "parsed_resume_data")
    op.drop_column("parsed_resume_data", "deleted_by")
    op.drop_column("parsed_resume_data", "deleted_at")

    op.drop_index("ix_generated_documents_deleted_at", "generated_documents")
    op.drop_column("generated_documents", "deleted_at")

    op.drop_index("ix_job_applications_deleted_at", "job_applications")
    op.drop_column("job_applications", "delete_reason")
    op.drop_column("job_applications", "deleted_by")
    op.drop_column("job_applications", "deleted_at")

    op.drop_column("user_profiles", "onboarding_completed")

    op.drop_index("ix_interview_answers_session_q", "interview_answers")
    op.drop_index("ix_interview_answers_session_id", "interview_answers")
    op.drop_table("interview_answers")

    op.drop_index("ix_interview_sessions_deleted_at", "interview_sessions")
    op.drop_index("ix_interview_sessions_status", "interview_sessions")
    op.drop_index("ix_interview_sessions_application_id", "interview_sessions")
    op.drop_index("ix_interview_sessions_user_id", "interview_sessions")
    op.drop_table("interview_sessions")

    op.drop_index("ix_support_ticket_messages_ticket_id", "support_ticket_messages")
    op.drop_table("support_ticket_messages")

    op.drop_index("ix_support_tickets_deleted_at", "support_tickets")
    op.drop_index("ix_support_tickets_assigned_admin_id", "support_tickets")
    op.drop_index("ix_support_tickets_status", "support_tickets")
    op.drop_index("ix_support_tickets_user_id", "support_tickets")
    op.drop_table("support_tickets")

    op.drop_index("ix_plans_deleted_at", "plans")
    op.drop_index("ix_plans_slug", "plans")
    op.drop_table("plans")

    op.drop_index("ix_product_events_user_created", "product_events")
    op.drop_index("ix_product_events_name_created", "product_events")
    op.drop_index("ix_product_events_created_at", "product_events")
    op.drop_index("ix_product_events_event_name", "product_events")
    op.drop_index("ix_product_events_user_id", "product_events")
    op.drop_table("product_events")

    op.drop_index("ix_audit_logs_entity", "audit_logs")
    op.drop_index("ix_audit_logs_actor_created", "audit_logs")
    op.drop_index("ix_audit_logs_created_at", "audit_logs")
    op.drop_index("ix_audit_logs_entity_id", "audit_logs")
    op.drop_index("ix_audit_logs_entity_type", "audit_logs")
    op.drop_index("ix_audit_logs_action", "audit_logs")
    op.drop_index("ix_audit_logs_actor_user_id", "audit_logs")
    op.drop_table("audit_logs")

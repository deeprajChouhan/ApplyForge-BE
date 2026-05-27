"""add job crawler tables and feature flag

Adds:
  - crawler_configs   (per-user crawler preferences and schedule settings)
  - crawled_jobs      (discovered job postings per user per crawl run)
  - job_crawler enum value to feature_flags

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-27

Fix: MySQL does not allow DEFAULT values on TEXT/BLOB columns.
     job_roles (TEXT) is now nullable=True with no server_default;
     the application layer always writes '[]' on INSERT.
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── crawler_configs ────────────────────────────────────────────────────
    op.create_table(
        "crawler_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="0"),
        # TEXT columns cannot have a DEFAULT in MySQL — app layer writes '[]' on INSERT
        sa.Column("job_roles", sa.Text(), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("salary_currency", sa.String(10), nullable=False, server_default="USD"),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("work_type", sa.String(20), nullable=False, server_default="any"),
        sa.Column("selected_resume_id", sa.Integer(),
                  sa.ForeignKey("parsed_resume_data.id", ondelete="SET NULL"), nullable=True),
        sa.Column("daily_goal", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", name="uq_crawler_config_user"),
    )
    op.create_index("ix_crawler_configs_user_id", "crawler_configs", ["user_id"])

    # ── crawled_jobs ───────────────────────────────────────────────────────
    op.create_table(
        "crawled_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("company", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("work_type", sa.String(30), nullable=True),
        sa.Column("salary_range", sa.String(100), nullable=True),
        # TEXT columns — no server_default (MySQL restriction)
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("apply_url", sa.String(1000), nullable=False),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("match_reason", sa.Text(), nullable=True),
        sa.Column("crawled_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("is_dismissed", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_saved", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("application_id", sa.Integer(),
                  sa.ForeignKey("job_applications.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "source", "external_id", name="uq_crawled_job_user_source_ext"),
    )
    op.create_index("ix_crawled_jobs_user_id", "crawled_jobs", ["user_id"])
    op.create_index("ix_crawled_jobs_user_crawled", "crawled_jobs", ["user_id", "crawled_at"])
    op.create_index("ix_crawled_jobs_crawled_at", "crawled_jobs", ["crawled_at"])

    # ── Extend the feature enum (MySQL ALTER TABLE) ────────────────────────
    op.execute(
        "ALTER TABLE user_features MODIFY COLUMN feature ENUM("
        "'jd_analyze','applications','kanban','resume','chat','multi_resume','job_crawler'"
        ") NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_crawled_jobs_user_crawled", "crawled_jobs")
    op.drop_index("ix_crawled_jobs_user_id", "crawled_jobs")
    op.drop_index("ix_crawled_jobs_crawled_at", "crawled_jobs")
    op.drop_table("crawled_jobs")

    op.drop_index("ix_crawler_configs_user_id", "crawler_configs")
    op.drop_table("crawler_configs")

    op.execute(
        "ALTER TABLE user_features MODIFY COLUMN feature ENUM("
        "'jd_analyze','applications','kanban','resume','chat','multi_resume'"
        ") NOT NULL"
    )

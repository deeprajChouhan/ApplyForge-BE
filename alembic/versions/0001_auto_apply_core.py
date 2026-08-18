"""auto-apply core: companies, jobs, job_sources

Also merges the two pre-existing alembic heads (0028 and ea40452e4ce3),
so this single migration both adds the new tables and reunifies history.

Revision ID: 0001_auto_apply_core
Revises: 0028, ea40452e4ce3
Create Date: 2026-08-18 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Tuple, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_auto_apply_core"
# Tuple down_revision = merge point for the two existing heads.
down_revision: Union[str, Tuple[str, ...], None] = ("0028", "ea40452e4ce3")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- companies ---------------------------------------------------------
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("ats_provider", sa.String(length=64), nullable=False),
        sa.Column("ats_slug", sa.String(length=255), nullable=False),
        sa.Column("careers_url", sa.String(length=1024), nullable=True),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("size_bucket", sa.String(length=32), nullable=True),
        sa.Column("industry", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "ats_provider", "ats_slug", name="uq_companies_ats_provider_ats_slug"
        ),
    )
    op.create_index(
        "ix_companies_ats_provider", "companies", ["ats_provider"], unique=False
    )

    # --- jobs ----------------------------------------------------------------
    job_remote_mode = sa.Enum(
        "onsite", "hybrid", "remote", "unknown", name="job_remote_mode"
    )
    job_submit_method = sa.Enum(
        "ats_api", "playwright", "extension", "manual", name="job_submit_method"
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("ats_provider", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column(
            "remote_mode",
            job_remote_mode,
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("employment_type", sa.String(length=64), nullable=True),
        sa.Column("seniority", sa.String(length=64), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("salary_currency", sa.String(length=3), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("description_html", sa.Text(), nullable=True),
        sa.Column("apply_url", sa.String(length=2048), nullable=False),
        sa.Column(
            "submit_method",
            job_submit_method,
            nullable=False,
            server_default="manual",
        ),
        sa.Column("jd_analysis_json", sa.JSON(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_jobs_company_id_companies", ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "ats_provider", "external_id", name="uq_jobs_ats_provider_external_id"
        ),
    )
    op.create_index("ix_jobs_company_id", "jobs", ["company_id"], unique=False)
    op.create_index("ix_jobs_ats_provider", "jobs", ["ats_provider"], unique=False)
    # Plain composite index (SQLite-compatible); MySQL will still use it efficiently
    # for is_active=True + ORDER BY last_seen_at queries.
    op.create_index(
        "ix_jobs_is_active_last_seen_at",
        "jobs",
        ["is_active", "last_seen_at"],
        unique=False,
    )

    # --- job_sources -----------------------------------------------------------
    op.create_table(
        "job_sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_job_sources_job_id_jobs", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_job_sources_job_id", "job_sources", ["job_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_job_sources_job_id", table_name="job_sources")
    op.drop_table("job_sources")

    op.drop_index("ix_jobs_is_active_last_seen_at", table_name="jobs")
    op.drop_index("ix_jobs_ats_provider", table_name="jobs")
    op.drop_index("ix_jobs_company_id", table_name="jobs")
    op.drop_table("jobs")

    bind = op.get_bind()
    sa.Enum(name="job_remote_mode").drop(bind, checkfirst=True)
    sa.Enum(name="job_submit_method").drop(bind, checkfirst=True)

    op.drop_index("ix_companies_ats_provider", table_name="companies")
    op.drop_table("companies")

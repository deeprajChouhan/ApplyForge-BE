"""auto apply engine: settings, events, runs, job_applications columns

Revision ID: 0003_auto_apply_engine
Revises: 0002_answer_library
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_auto_apply_engine"
down_revision = "0002_answer_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- auto_apply_settings ---
    op.create_table(
        "auto_apply_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default="0"
        ),
        sa.Column("target_titles_json", sa.JSON(), nullable=True),
        sa.Column("locations_json", sa.JSON(), nullable=True),
        sa.Column(
            "remote_only", sa.Boolean(), nullable=False, server_default="0"
        ),
        sa.Column(
            "min_match_score", sa.Integer(), nullable=False, server_default="70"
        ),
        sa.Column(
            "daily_cap", sa.Integer(), nullable=False, server_default="20"
        ),
        sa.Column(
            "weekly_cap", sa.Integer(), nullable=False, server_default="100"
        ),
        sa.Column("excluded_companies_json", sa.JSON(), nullable=True),
        sa.Column("excluded_keywords_json", sa.JSON(), nullable=True),
        sa.Column("default_kit_id", sa.Integer(), nullable=True),
        sa.Column(
            "fully_automatic", sa.Boolean(), nullable=False, server_default="0"
        ),
        sa.Column("paused_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )

    # --- application_events ---
    op.create_table(
        "application_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("job_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_application_events_application_id",
        "application_events",
        ["application_id"],
    )
    op.create_index(
        "ix_application_events_event_type",
        "application_events",
        ["event_type"],
    )
    op.create_index(
        "ix_application_events_created_at",
        "application_events",
        ["created_at"],
    )

    # --- auto_apply_runs ---
    op.create_table(
        "auto_apply_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column(
            "jobs_considered", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "jobs_queued", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("error_text", sa.Text(), nullable=True),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_auto_apply_runs_user_id", "auto_apply_runs", ["user_id"]
    )
    op.create_index(
        "ix_auto_apply_runs_started_at", "auto_apply_runs", ["started_at"]
    )

    # --- job_applications: new nullable columns (purely additive) ---
    op.add_column(
        "job_applications",
        sa.Column("auto_apply_stage", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "job_applications", sa.Column("match_score", sa.Integer(), nullable=True)
    )
    op.add_column(
        "job_applications", sa.Column("match_reasons_json", sa.JSON(), nullable=True)
    )
    op.add_column(
        "job_applications",
        sa.Column("submit_method", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "job_applications",
        sa.Column("submission_evidence_url", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "job_applications", sa.Column("submitted_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "job_applications", sa.Column("job_id", sa.Integer(), nullable=True)
    )

    op.create_index(
        "ix_job_applications_auto_apply_stage",
        "job_applications",
        ["user_id", "auto_apply_stage"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_job_applications_auto_apply_stage", table_name="job_applications"
    )

    op.drop_column("job_applications", "job_id")
    op.drop_column("job_applications", "submitted_at")
    op.drop_column("job_applications", "submission_evidence_url")
    op.drop_column("job_applications", "submit_method")
    op.drop_column("job_applications", "match_reasons_json")
    op.drop_column("job_applications", "match_score")
    op.drop_column("job_applications", "auto_apply_stage")

    op.drop_index("ix_auto_apply_runs_started_at", table_name="auto_apply_runs")
    op.drop_index("ix_auto_apply_runs_user_id", table_name="auto_apply_runs")
    op.drop_table("auto_apply_runs")

    op.drop_index(
        "ix_application_events_created_at", table_name="application_events"
    )
    op.drop_index(
        "ix_application_events_event_type", table_name="application_events"
    )
    op.drop_index(
        "ix_application_events_application_id", table_name="application_events"
    )
    op.drop_table("application_events")

    op.drop_table("auto_apply_settings")

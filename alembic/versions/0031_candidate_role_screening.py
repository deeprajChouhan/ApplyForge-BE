"""Recruiter OS Phase 1: role-specific screening on rec_applications.

Adds role-specific screening data to rec_applications (the CandidateRole join),
country_code to rec_roles, and market_preferences to rec_candidate_profiles.
All additive; no data backfill required. Existing behaviour is preserved.

Revision ID: 0031_candidate_role_screening
Revises: 0030_merge_heads
Create Date: 2026-09-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "0031_candidate_role_screening"
down_revision = "0030_merge_heads"
branch_labels = None
depends_on = None


SCREENING_OUTCOME = sa.Enum("suitable", "maybe", "not_suitable", name="screeningoutcome")
WORK_MODEL = sa.Enum("remote", "hybrid", "onsite", "flexible", name="workmodel")
RELOCATION = sa.Enum("yes", "no", "conditional", name="relocationpreference")


def upgrade() -> None:
    bind = op.get_bind()

    # Enum types must be pre-created on Postgres. On MySQL/SQLite the ENUM
    # column definition inlines them, so create_type() is a no-op.
    SCREENING_OUTCOME.create(bind, checkfirst=True)
    WORK_MODEL.create(bind, checkfirst=True)
    RELOCATION.create(bind, checkfirst=True)

    # ── rec_roles ────────────────────────────────────────────────────────
    with op.batch_alter_table("rec_roles") as b:
        b.add_column(sa.Column("country_code", sa.String(length=2), nullable=True))
    op.create_index(
        "ix_rec_roles_country_code", "rec_roles", ["country_code"]
    )

    # ── rec_candidate_profiles ──────────────────────────────────────────
    with op.batch_alter_table("rec_candidate_profiles") as b:
        b.add_column(sa.Column("market_preferences", sa.JSON(), nullable=True))

    # ── rec_applications: role-specific screening block ─────────────────
    with op.batch_alter_table("rec_applications") as b:
        b.add_column(sa.Column("screening_outcome", SCREENING_OUTCOME, nullable=True))
        b.add_column(sa.Column("screening_completed_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("screening_completed_by", sa.Integer(), nullable=True))
        b.add_column(sa.Column("assigned_recruiter_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("recruiter_summary", sa.Text(), nullable=True))
        b.add_column(sa.Column("candidate_motivation", sa.Text(), nullable=True))
        b.add_column(sa.Column("internal_notes", sa.Text(), nullable=True))
        b.add_column(sa.Column("motivation_categories", sa.JSON(), nullable=True))
        b.add_column(sa.Column("expected_compensation", sa.JSON(), nullable=True))
        b.add_column(sa.Column("current_compensation", sa.JSON(), nullable=True))
        b.add_column(sa.Column("notice_period", sa.JSON(), nullable=True))
        b.add_column(sa.Column("availability_date", sa.Date(), nullable=True))
        b.add_column(
            sa.Column(
                "availability_immediate",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        b.add_column(sa.Column("preferred_work_model", WORK_MODEL, nullable=True))
        b.add_column(sa.Column("preferred_location", sa.String(length=200), nullable=True))
        b.add_column(sa.Column("relocation", RELOCATION, nullable=True))
        b.add_column(sa.Column("relocation_notes", sa.String(length=500), nullable=True))
        # client_visibility JSON: default '{}' — application-level default in
        # the model handles this; leave nullable to keep MySQL/SQLite happy.
        b.add_column(sa.Column("client_visibility", sa.JSON(), nullable=True))

    op.create_index(
        "ix_rec_applications_assigned_recruiter",
        "rec_applications",
        ["assigned_recruiter_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_rec_applications_assigned_recruiter", table_name="rec_applications")
    with op.batch_alter_table("rec_applications") as b:
        for col in (
            "client_visibility",
            "relocation_notes",
            "relocation",
            "preferred_location",
            "preferred_work_model",
            "availability_immediate",
            "availability_date",
            "notice_period",
            "current_compensation",
            "expected_compensation",
            "motivation_categories",
            "internal_notes",
            "candidate_motivation",
            "recruiter_summary",
            "assigned_recruiter_id",
            "screening_completed_by",
            "screening_completed_at",
            "screening_outcome",
        ):
            b.drop_column(col)

    with op.batch_alter_table("rec_candidate_profiles") as b:
        b.drop_column("market_preferences")

    op.drop_index("ix_rec_roles_country_code", table_name="rec_roles")
    with op.batch_alter_table("rec_roles") as b:
        b.drop_column("country_code")

    bind = op.get_bind()
    RELOCATION.drop(bind, checkfirst=True)
    WORK_MODEL.drop(bind, checkfirst=True)
    SCREENING_OUTCOME.drop(bind, checkfirst=True)

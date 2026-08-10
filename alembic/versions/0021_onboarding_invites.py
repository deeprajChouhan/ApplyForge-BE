"""onboarding + invites

Phase 5.5 — self-serve onboarding. Adds lifecycle status + trial clock to
rec_agencies, and the rec_agency_invites table for the seat invite/claim flow.
Idempotent: safe whether the tables were created by the startup checkfirst hook
or by earlier migrations.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    cols = {c["name"] for c in insp.get_columns("rec_agencies")}
    if "status" not in cols:
        op.add_column(
            "rec_agencies",
            sa.Column(
                "status",
                sa.Enum("pending", "active", "suspended", name="agencystatus"),
                nullable=False,
                server_default="active",
            ),
        )
    if "trial_ends_at" not in cols:
        op.add_column("rec_agencies", sa.Column("trial_ends_at", sa.DateTime(), nullable=True))

    if not insp.has_table("rec_agency_invites"):
        op.create_table(
            "rec_agency_invites",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "agency_id",
                sa.Integer(),
                sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column(
                "role",
                sa.Enum("owner", "recruiter", name="recruiterseatrole"),
                nullable=False,
                server_default="recruiter",
            ),
            sa.Column("token", sa.String(64), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_rec_agency_invites_agency_id", "rec_agency_invites", ["agency_id"])
        op.create_index("ix_rec_agency_invites_email", "rec_agency_invites", ["email"])
        op.create_index("ix_rec_agency_invites_token", "rec_agency_invites", ["token"], unique=True)
        op.create_index("ix_rec_agency_invites_status", "rec_agency_invites", ["status"])


def downgrade() -> None:
    op.drop_table("rec_agency_invites")
    op.drop_column("rec_agencies", "trial_ends_at")
    op.drop_column("rec_agencies", "status")

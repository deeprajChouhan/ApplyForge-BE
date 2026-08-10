"""agency plans + seats

Phase 5.1 — adds billing tier and seat limit to recruiter agencies.
Adds rec_agencies.plan (enum: free/pro/enterprise, default free) and
rec_agencies.seat_limit (nullable int; NULL = plan default / unlimited).

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("rec_agencies")}
    if "plan" not in cols:
        op.add_column(
            "rec_agencies",
            sa.Column(
                "plan",
                sa.Enum("free", "pro", "enterprise", name="agencyplan"),
                nullable=False,
                server_default="free",
            ),
        )
    if "seat_limit" not in cols:
        op.add_column("rec_agencies", sa.Column("seat_limit", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("rec_agencies", "seat_limit")
    op.drop_column("rec_agencies", "plan")

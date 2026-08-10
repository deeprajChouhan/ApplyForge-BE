"""usage metering

Phase 5.2 — append-only per-agency usage events (rec_usage_events).

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("rec_usage_events"):
        return
    op.create_table(
        "rec_usage_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_rec_usage_events_agency_id", "rec_usage_events", ["agency_id"])
    op.create_index("ix_rec_usage_events_kind", "rec_usage_events", ["kind"])
    op.create_index("ix_rec_usage_events_created_at", "rec_usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("rec_usage_events")

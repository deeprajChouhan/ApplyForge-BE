"""user extension state

Adds the user_extension_state table backing the ApplyForge Job Clipper:
  - connected_at        (datetime, nullable)
  - last_seen_at        (datetime, nullable)
  - promo_dismissed_at  (datetime, nullable)

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_extension_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("connected_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("promo_dismissed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_user_extension_state_user_id",
        "user_extension_state",
        ["user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_user_extension_state_user_id", table_name="user_extension_state")
    op.drop_table("user_extension_state")

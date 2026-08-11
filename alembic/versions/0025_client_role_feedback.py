"""client role feedback

Adds rec_role_feedback: client-side sentiment + comments on candidates in a
role, submitted through a public share token. Idempotent.

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa


revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("rec_role_feedback"):
        op.create_table(
            "rec_role_feedback",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "agency_id",
                sa.Integer(),
                sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "role_id",
                sa.Integer(),
                sa.ForeignKey("rec_roles.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "share_token_id",
                sa.Integer(),
                sa.ForeignKey("rec_role_share_tokens.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("candidate_id", sa.Integer(), nullable=True),
            # Sentiment is +1 / 0 / -1; NULL means comment-only.
            sa.Column("sentiment", sa.Integer(), nullable=True),
            sa.Column("body", sa.Text(), nullable=True),
            sa.Column("client_name", sa.String(200), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_rec_role_feedback_agency_id", "rec_role_feedback", ["agency_id"])
        op.create_index("ix_rec_role_feedback_role_id", "rec_role_feedback", ["role_id"])


def downgrade() -> None:
    op.drop_table("rec_role_feedback")

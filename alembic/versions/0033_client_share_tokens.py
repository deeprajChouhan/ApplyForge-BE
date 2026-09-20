"""client share tokens

Adds rec_client_share_tokens for the public client-status share link
(mirrors rec_role_share_tokens). Idempotent.

Revision ID: 0033_client_share_tokens
Revises: 0032_recruiter_os_phases_2_7
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa


revision = "0033_client_share_tokens"
down_revision = "0032_recruiter_os_phases_2_7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("rec_client_share_tokens"):
        op.create_table(
            "rec_client_share_tokens",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "agency_id",
                sa.Integer(),
                sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "client_id",
                sa.Integer(),
                sa.ForeignKey("rec_clients.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("token", sa.String(64), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_viewed_at", sa.DateTime(), nullable=True),
            sa.Column("ai_summary", sa.Text(), nullable=True),
            sa.Column("ai_summary_generated_at", sa.DateTime(), nullable=True),
            sa.Column(
                "ai_summary_used_llm",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(
            "ix_rec_client_share_tokens_token",
            "rec_client_share_tokens",
            ["token"],
            unique=True,
        )
        op.create_index(
            "ix_rec_client_share_tokens_client_id",
            "rec_client_share_tokens",
            ["client_id"],
        )
        op.create_index(
            "ix_rec_client_share_tokens_agency_id",
            "rec_client_share_tokens",
            ["agency_id"],
        )


def downgrade() -> None:
    op.drop_table("rec_client_share_tokens")

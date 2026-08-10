"""application notes + share tokens

Adds:
  - rec_application_notes: per-application activity log (user + system entries).
  - rec_role_share_tokens: one active token per role for client-facing sharing.

Idempotent.

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa


revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("rec_application_notes"):
        op.create_table(
            "rec_application_notes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "agency_id",
                sa.Integer(),
                sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "application_id",
                sa.Integer(),
                sa.ForeignKey("rec_applications.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("author_recruiter_id", sa.Integer(), nullable=True),
            sa.Column("author_name", sa.String(200), nullable=True),
            sa.Column("kind", sa.String(20), nullable=False, server_default="note"),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(
            "ix_rec_application_notes_agency_id", "rec_application_notes", ["agency_id"]
        )
        op.create_index(
            "ix_rec_application_notes_application_id",
            "rec_application_notes",
            ["application_id"],
        )

    if not insp.has_table("rec_role_share_tokens"):
        op.create_table(
            "rec_role_share_tokens",
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
            sa.Column("token", sa.String(64), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_viewed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(
            "ix_rec_role_share_tokens_token",
            "rec_role_share_tokens",
            ["token"],
            unique=True,
        )
        op.create_index(
            "ix_rec_role_share_tokens_role_id",
            "rec_role_share_tokens",
            ["role_id"],
        )


def downgrade() -> None:
    op.drop_table("rec_role_share_tokens")
    op.drop_table("rec_application_notes")

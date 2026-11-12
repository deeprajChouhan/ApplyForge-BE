"""per-recruiter role visibility

- rec_recruiter_role_restrictions: a row marks a seat as "only selected roles".
- rec_recruiter_role_grants: the roles such a seat may see.

No existing tables change — every seat keeps seeing every role until an owner
restricts it. Idempotent (the tables are also created at boot by
ensure_recruiter_tables). Stays inside the rec_* data wall.

Revision ID: 0036_recruiter_role_visibility
Revises: 0035_partner_api
Create Date: 2026-11-12
"""
from alembic import op
import sqlalchemy as sa


revision = "0036_recruiter_role_visibility"
down_revision = "0035_partner_api"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("rec_recruiter_role_restrictions"):
        op.create_table(
            "rec_recruiter_role_restrictions",
            sa.Column("recruiter_id", sa.Integer, sa.ForeignKey("rec_recruiters.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("agency_id", sa.Integer, sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("set_by_recruiter_id", sa.Integer, nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        )
    if not insp.has_table("rec_recruiter_role_grants"):
        op.create_table(
            "rec_recruiter_role_grants",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("agency_id", sa.Integer, sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("recruiter_id", sa.Integer, sa.ForeignKey("rec_recruiters.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("role_id", sa.Integer, sa.ForeignKey("rec_roles.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("granted_by_recruiter_id", sa.Integer, nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
            sa.UniqueConstraint("recruiter_id", "role_id", name="uq_rec_role_grant"),
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for t in ("rec_recruiter_role_grants", "rec_recruiter_role_restrictions"):
        if insp.has_table(t):
            op.drop_table(t)

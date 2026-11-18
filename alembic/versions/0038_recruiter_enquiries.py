"""recruiter website enquiries

- rec_enquiries: contact-form submissions from the public recruiter site,
  triaged by the operator in the admin console. No agency_id and no FKs —
  prospects aren't tenants yet. Stays inside the rec_* data wall.

Idempotent (also created at boot by ensure_recruiter_tables).

Revision ID: 0038_recruiter_enquiries
Revises: 0037_recruiter_sourced
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa


revision = "0038_recruiter_enquiries"
down_revision = "0037_recruiter_sourced"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table("rec_enquiries"):
        return
    op.create_table(
        "rec_enquiries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("topic", sa.String(20), nullable=False, server_default="sales"),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("company", sa.String(200)),
        sa.Column("company_type", sa.String(30)),
        sa.Column("team_size", sa.String(30)),
        sa.Column("phone", sa.String(60)),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("source_page", sa.String(300)),
        sa.Column("admin_notes", sa.Text),
        sa.Column("handled_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_rec_enquiries_topic", "rec_enquiries", ["topic"])
    op.create_index("ix_rec_enquiries_status", "rec_enquiries", ["status"])
    op.create_index("ix_rec_enquiries_email", "rec_enquiries", ["email"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table("rec_enquiries"):
        op.drop_table("rec_enquiries")

"""application tailoring (evidence-mapped ATS resume tailoring)

Adds application_tailorings: one row per application holding the tailored
resume overlay + keyword/evidence report. Consumer-only; no recruiter tables.

Idempotent: skipped if the table already exists.

Revision ID: 0041_application_tailoring
Revises: 0040_extension_settings
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0041_application_tailoring"
down_revision = "0040_extension_settings"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    if _has_table("application_tailorings"):
        return
    op.create_table(
        "application_tailorings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", sa.Integer, sa.ForeignKey("job_applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_resume_id", sa.Integer, sa.ForeignKey("parsed_resume_data.id", ondelete="SET NULL"), nullable=True),
        sa.Column("generated_document_id", sa.Integer, sa.ForeignKey("generated_documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("tailored_json", sa.Text, nullable=False),
        sa.Column("report_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_application_tailorings_user_id", "application_tailorings", ["user_id"])
    op.create_index("ix_application_tailorings_application_id", "application_tailorings", ["application_id"], unique=True)


def downgrade() -> None:
    if _has_table("application_tailorings"):
        op.drop_index("ix_application_tailorings_application_id", table_name="application_tailorings")
        op.drop_index("ix_application_tailorings_user_id", table_name="application_tailorings")
        op.drop_table("application_tailorings")

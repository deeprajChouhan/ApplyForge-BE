"""application notes

Adds job_applications.notes: free-text notes the user keeps on an
application (Application detail redesign, Notes inspector section).
Consumer-only; no recruiter tables.

Idempotent: skipped if the column already exists.

Revision ID: 0042_application_notes
Revises: 0041_application_tailoring
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0042_application_notes"
down_revision = "0041_application_tailoring"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    cols = sa.inspect(op.get_bind()).get_columns(table)
    return any(c["name"] == column for c in cols)


def upgrade() -> None:
    if _has_column("job_applications", "notes"):
        return
    op.add_column("job_applications", sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    if not _has_column("job_applications", "notes"):
        return
    with op.batch_alter_table("job_applications") as batch:
        batch.drop_column("notes")

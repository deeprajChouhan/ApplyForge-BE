"""extension job-hunting settings

Adds user_extension_state.settings_json (JSON, nullable) holding the
per-user Job Hunting → Extension preferences. Consumer-only; no recruiter
tables touched.

Idempotent: skipped if the column already exists.

Revision ID: 0040_extension_settings
Revises: 0039_email_system
Create Date: 2026-11-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0040_extension_settings"
down_revision = "0039_email_system"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    if not _has_column("user_extension_state", "settings_json"):
        op.add_column("user_extension_state", sa.Column("settings_json", sa.JSON, nullable=True))


def downgrade() -> None:
    if _has_column("user_extension_state", "settings_json"):
        op.drop_column("user_extension_state", "settings_json")

"""client contact fields

Adds contact + notes columns to rec_clients so the client detail page can hold
the primary contact, phone, website, address, and free-form notes recruiters
maintain per client. Idempotent.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def _has_col(insp, table: str, col: str) -> bool:
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    for col, ddl in (
        ("primary_contact_name", sa.Column("primary_contact_name", sa.String(200), nullable=True)),
        ("contact_email", sa.Column("contact_email", sa.String(255), nullable=True)),
        ("contact_phone", sa.Column("contact_phone", sa.String(60), nullable=True)),
        ("website", sa.Column("website", sa.String(300), nullable=True)),
        ("address", sa.Column("address", sa.String(500), nullable=True)),
        ("notes", sa.Column("notes", sa.Text(), nullable=True)),
    ):
        if not _has_col(insp, "rec_clients", col):
            op.add_column("rec_clients", ddl)


def downgrade() -> None:
    for col in ("notes", "address", "website", "contact_phone", "contact_email", "primary_contact_name"):
        op.drop_column("rec_clients", col)

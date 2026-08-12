"""linkedin capture: candidate source + linkedin_url

Extends the recruiter platform's candidate pool with a LinkedIn capture path:
- CandidateSource enum gains `linkedin`
- rec_candidate_profiles.linkedin_url (nullable, indexed) — canonical URL
  used as the per-agency dedup key for the Chrome-extension capture flow.

Idempotent — safe on databases that predate Alembic tracking for the
recruiter tables (checkfirst bootstrap), and on Postgres where the enum
value must be added out-of-transaction.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-11
"""
from alembic import op
import sqlalchemy as sa


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def _has_col(insp, table: str, col: str) -> bool:
    return col in {c["name"] for c in insp.get_columns(table)}


def _has_index(insp, table: str, name: str) -> bool:
    return name in {ix["name"] for ix in insp.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # ── enum: candidatesource += 'linkedin' ─────────────────────────────
    # Postgres stores enums as separate types and can't add values inside a
    # transaction. Everywhere else (SQLite, MySQL) the enum is just a CHECK
    # constraint or a VARCHAR, so nothing to do here.
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE candidatesource ADD VALUE IF NOT EXISTS 'linkedin'")

    # ── rec_candidate_profiles.linkedin_url ─────────────────────────────
    if not _has_col(insp, "rec_candidate_profiles", "linkedin_url"):
        op.add_column(
            "rec_candidate_profiles",
            sa.Column("linkedin_url", sa.String(500), nullable=True),
        )
    if not _has_index(insp, "rec_candidate_profiles", "ix_rec_candidate_profiles_linkedin_url"):
        op.create_index(
            "ix_rec_candidate_profiles_linkedin_url",
            "rec_candidate_profiles",
            ["linkedin_url"],
        )


def downgrade() -> None:
    # Enum values are intentionally not removed on downgrade (Postgres can't
    # drop enum values without rewriting the type, and rows may already use it).
    op.drop_index(
        "ix_rec_candidate_profiles_linkedin_url",
        table_name="rec_candidate_profiles",
    )
    op.drop_column("rec_candidate_profiles", "linkedin_url")

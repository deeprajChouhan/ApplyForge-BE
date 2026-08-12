"""mysql: add 'linkedin' to candidate source enum

Migration 0026 extended the Postgres enum via `ALTER TYPE ... ADD VALUE`
and no-op'd on other dialects. Production runs MySQL, where an ENUM
column must be redefined with `ALTER TABLE ... MODIFY COLUMN` to add a
value — otherwise INSERTing `source='linkedin'` silently truncates to
empty and MySQL raises:

    (1265, "Data truncated for column 'source' at row 1")

This migration adds that MySQL branch. Idempotent — re-running the
ALTER just re-declares the same enum values. No-op on Postgres (0026
already added the value) and SQLite (enum is stored as VARCHAR, so any
string is accepted).

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-12
"""
from alembic import op


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


# Must mirror app.recruiter.enums.CandidateSource exactly.
CANDIDATE_SOURCE_VALUES = ("bulk_cv", "linkedin", "manual", "ats_sync", "referral")


def _enum_values_sql(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.execute(
            "ALTER TABLE rec_candidate_profiles "
            f"MODIFY COLUMN source ENUM({_enum_values_sql(CANDIDATE_SOURCE_VALUES)}) "
            "NOT NULL DEFAULT 'bulk_cv'"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        # Drop 'linkedin' from the enum definition. Any rows still holding
        # that value would break; the migration is one-way in practice, but
        # we provide a symmetric definition for completeness.
        without_linkedin = tuple(v for v in CANDIDATE_SOURCE_VALUES if v != "linkedin")
        op.execute(
            "ALTER TABLE rec_candidate_profiles "
            f"MODIFY COLUMN source ENUM({_enum_values_sql(without_linkedin)}) "
            "NOT NULL DEFAULT 'bulk_cv'"
        )

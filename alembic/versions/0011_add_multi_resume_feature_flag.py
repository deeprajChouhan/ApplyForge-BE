"""add multi_resume to featureflag enum

Extends the featureflag ENUM in user_features and usage_events tables
to include the 'multi_resume' value added to the FeatureFlag Python enum.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-09
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

# Full ordered list of ENUM values (append only — never reorder)
_ALL_FLAGS = "'jd_analyze','applications','kanban','resume','chat','multi_resume'"
_OLD_FLAGS = "'jd_analyze','applications','kanban','resume','chat'"


def upgrade() -> None:
    # MySQL stores ENUMs inline — ALTER TABLE MODIFY COLUMN is the only way
    # to extend them.  We use NOT NULL / NULL to match each column's
    # original DDL from migration 0003.
    op.execute(
        f"ALTER TABLE user_features "
        f"MODIFY COLUMN feature ENUM({_ALL_FLAGS}) NOT NULL"
    )
    op.execute(
        f"ALTER TABLE usage_events "
        f"MODIFY COLUMN feature ENUM({_ALL_FLAGS}) NULL"
    )


def downgrade() -> None:
    # Reverse: shrink back to original five values.
    # NOTE: this will fail if any rows currently hold 'multi_resume'.
    op.execute(
        f"ALTER TABLE user_features "
        f"MODIFY COLUMN feature ENUM({_OLD_FLAGS}) NOT NULL"
    )
    op.execute(
        f"ALTER TABLE usage_events "
        f"MODIFY COLUMN feature ENUM({_OLD_FLAGS}) NULL"
    )

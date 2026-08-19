"""auto-apply: extra preference fields (willing_to_relocate, min_salary,
salary_currency, default_resume_parse_id).

Revision ID: 0004_auto_apply_extra_fields
Revises: 0003_auto_apply_engine
Create Date: 2026-08-19 00:00:00.000000

All columns are nullable so existing rows remain valid without a data
migration.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_auto_apply_extra_fields"
down_revision: Union[str, None] = "0003_auto_apply_engine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "auto_apply_settings",
        sa.Column("willing_to_relocate", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "auto_apply_settings",
        sa.Column("min_salary", sa.Integer(), nullable=True),
    )
    op.add_column(
        "auto_apply_settings",
        sa.Column("salary_currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "auto_apply_settings",
        sa.Column("default_resume_parse_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("auto_apply_settings", "default_resume_parse_id")
    op.drop_column("auto_apply_settings", "salary_currency")
    op.drop_column("auto_apply_settings", "min_salary")
    op.drop_column("auto_apply_settings", "willing_to_relocate")

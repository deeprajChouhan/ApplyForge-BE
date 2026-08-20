"""Add jd_features_json to jobs — cached RCMS extractor output.

Revision ID: 0005_job_features
Revises: 0004_auto_apply_extra_fields
Create Date: 2026-08-20
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_job_features"
down_revision: Union[str, None] = "0004_auto_apply_extra_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("jd_features_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "jd_features_json")

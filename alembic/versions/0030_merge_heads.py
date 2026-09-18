"""merge auto-apply branch into main tree

Alembic saw two heads because the auto-apply feature tree
(0001_auto_apply_core → 0002_answer_library → 0003_auto_apply_engine →
0004_auto_apply_extra_fields → 0005_job_features) grew in parallel with
the main tree that continued past its own merge point onto 0029_resume_templates.
Both branches are additive DDL only; this is a pure merge revision so
`alembic upgrade head` resolves to a single head again.

Revision ID: 0030_merge_heads
Revises: 0029, 0005_job_features
Create Date: 2026-09-18
"""
from __future__ import annotations

from typing import Sequence, Union


revision: str = "0030_merge_heads"
down_revision: Union[str, Sequence[str], None] = ("0029", "0005_job_features")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No schema changes — merge revision only."""
    pass


def downgrade() -> None:
    """No schema changes — merge revision only."""
    pass

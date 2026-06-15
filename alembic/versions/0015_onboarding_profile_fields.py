"""onboarding profile fields

Adds the raw onboarding answers as columns on user_profiles so they are
persisted (not just folded into a knowledge_documents summary):
  - current_role            (string)
  - career_goals            (text)
  - target_roles            (text, JSON list[str])
  - preferred_locations     (text, JSON list[str])
  - salary_expectation      (string)
  - deal_breakers           (text, JSON list[str])

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_profiles", sa.Column("current_role", sa.String(length=255), nullable=True))
    op.add_column("user_profiles", sa.Column("career_goals", sa.Text(), nullable=True))
    op.add_column("user_profiles", sa.Column("target_roles", sa.Text(), nullable=True))
    op.add_column("user_profiles", sa.Column("preferred_locations", sa.Text(), nullable=True))
    op.add_column("user_profiles", sa.Column("salary_expectation", sa.String(length=100), nullable=True))
    op.add_column("user_profiles", sa.Column("deal_breakers", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_profiles", "deal_breakers")
    op.drop_column("user_profiles", "salary_expectation")
    op.drop_column("user_profiles", "preferred_locations")
    op.drop_column("user_profiles", "target_roles")
    op.drop_column("user_profiles", "career_goals")
    op.drop_column("user_profiles", "current_role")

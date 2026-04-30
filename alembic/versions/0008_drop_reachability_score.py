"""Drop reachability_score column from job_applications

Revision ID: 0008_drop_reachability_score
Revises: 0007_linkedin_connections
Create Date: 2026-04-30
"""
from alembic import op
import sqlalchemy as sa

revision = '0008_drop_reachability_score'
down_revision = '0007_linkedin_connections'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column('job_applications', 'reachability_score')


def downgrade() -> None:
    op.add_column(
        'job_applications',
        sa.Column('reachability_score', sa.Float(), nullable=True),
    )

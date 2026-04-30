"""Add priority score columns to job_applications

Revision ID: 0006_priority_scores
Revises: 0005_nullable_chunk_embedding
Create Date: 2026-04-30
"""
from alembic import op
import sqlalchemy as sa

revision = '0006_priority_scores'
down_revision = '0005_nullable_chunk_embedding'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('job_applications', sa.Column('fit_score',          sa.Float(), nullable=True))
    op.add_column('job_applications', sa.Column('competition_score',  sa.Float(), nullable=True))
    op.add_column('job_applications', sa.Column('reachability_score', sa.Float(), nullable=True))
    op.add_column('job_applications', sa.Column('priority_score',     sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('job_applications', 'priority_score')
    op.drop_column('job_applications', 'reachability_score')
    op.drop_column('job_applications', 'competition_score')
    op.drop_column('job_applications', 'fit_score')

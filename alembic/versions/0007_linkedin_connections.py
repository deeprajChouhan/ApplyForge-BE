"""Create linkedin_connections table (Phase 2 -- LinkedIn CSV ingestion)

Revision ID: 0007_linkedin_connections
Revises: 0006_priority_scores
Create Date: 2026-04-30
"""
from alembic import op
import sqlalchemy as sa

revision = '0007_linkedin_connections'
down_revision = '0006_priority_scores'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'linkedin_connections',
        sa.Column('id',           sa.Integer(),     nullable=False),
        sa.Column('user_id',      sa.Integer(),     nullable=False),
        sa.Column('full_name',    sa.String(255),   nullable=False),
        sa.Column('company',      sa.String(255),   nullable=True),
        sa.Column('position',     sa.String(255),   nullable=True),
        sa.Column('connected_on', sa.Date(),        nullable=True),
        sa.Column('created_at',   sa.DateTime(),    nullable=True),
        sa.Column('updated_at',   sa.DateTime(),    nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'full_name', name='uq_linkedin_conn_user_name'),
    )
    op.create_index('ix_linkedin_connections_user_id', 'linkedin_connections', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_linkedin_connections_user_id', table_name='linkedin_connections')
    op.drop_table('linkedin_connections')

"""Add phone_number and age to user_profiles

Revision ID: 0004_userprofile_phone_age
Revises: ea40452e4ce3
Create Date: 2026-04-28
"""
from alembic import op
import sqlalchemy as sa

revision = '0004_userprofile_phone_age'
down_revision = '0003_saas_features'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user_profiles', sa.Column('phone_number', sa.String(length=50), nullable=True))
    op.add_column('user_profiles', sa.Column('age', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('user_profiles', 'age')
    op.drop_column('user_profiles', 'phone_number')

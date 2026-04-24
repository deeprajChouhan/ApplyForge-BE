"""saas_features

Adds SaaS multi-tenant fields and tables:
- users: role, plan, subscription_status, token_budget_monthly
- user_features: per-user feature flag grants
- usage_ledger: monthly token usage aggregation
- usage_events: granular per-call token log

Revision ID: 0003_saas_features
Revises: ea40452e4ce3
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = '0003_saas_features'
down_revision = 'ea40452e4ce3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users: new columns ─────────────────────────────────────────────────
    op.add_column('users', sa.Column('role', sa.Enum('admin', 'user', name='userrole'), nullable=False, server_default='user'))
    op.add_column('users', sa.Column('plan', sa.Enum('free', 'pro', 'enterprise', name='plantier'), nullable=False, server_default='free'))
    op.add_column('users', sa.Column('subscription_status', sa.Enum('active', 'trialing', 'cancelled', 'past_due', name='subscriptionstatus'), nullable=False, server_default='active'))
    op.add_column('users', sa.Column('token_budget_monthly', sa.Integer(), nullable=False, server_default='50000'))

    op.create_index('ix_users_role', 'users', ['role'])
    op.create_index('ix_users_plan', 'users', ['plan'])

    # ── user_features ──────────────────────────────────────────────────────
    op.create_table(
        'user_features',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('feature', sa.Enum('jd_analyze', 'applications', 'kanban', 'resume', 'chat', name='featureflag'), nullable=False, index=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint('user_id', 'feature', name='uq_user_feature'),
    )

    # ── usage_ledger ───────────────────────────────────────────────────────
    op.create_table(
        'usage_ledger',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('month_year', sa.String(7), nullable=False, index=True),
        sa.Column('tokens_used', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('api_calls', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'month_year', name='uq_ledger_user_month'),
    )

    # ── usage_events ───────────────────────────────────────────────────────
    op.create_table(
        'usage_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('feature', sa.Enum('jd_analyze', 'applications', 'kanban', 'resume', 'chat', name='featureflag'), nullable=True, index=True),
        sa.Column('endpoint', sa.String(100), nullable=False),
        sa.Column('tokens_in', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tokens_out', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), index=True),
    )


def downgrade() -> None:
    op.drop_table('usage_events')
    op.drop_table('usage_ledger')
    op.drop_table('user_features')

    op.drop_index('ix_users_plan', table_name='users')
    op.drop_index('ix_users_role', table_name='users')
    op.drop_column('users', 'token_budget_monthly')
    op.drop_column('users', 'subscription_status')
    op.drop_column('users', 'plan')
    op.drop_column('users', 'role')

"""Usage metering + rolling 24h quotas.

- users.token_budget_daily (NULL = plan default), users.usage_window_reset_at
- usage_events.kind ("llm" | "embedding"), user_id nullable (anonymous/system spend),
  composite index (user_id, created_at) for the rolling-window query.

Revision ID: 0044_usage_metering_quotas
Revises: 0043_chat_threads
"""
from alembic import op
import sqlalchemy as sa

revision = "0044_usage_metering_quotas"
down_revision = "0043_chat_threads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("token_budget_daily", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("usage_window_reset_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("usage_events") as batch:
        batch.add_column(sa.Column("kind", sa.String(length=20), nullable=False, server_default="llm"))
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=True)
    op.create_index("ix_usage_events_kind", "usage_events", ["kind"])
    op.create_index("ix_usage_events_user_created", "usage_events", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_events_user_created", table_name="usage_events")
    op.drop_index("ix_usage_events_kind", table_name="usage_events")
    op.execute("DELETE FROM usage_events WHERE user_id IS NULL")
    with op.batch_alter_table("usage_events") as batch:
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("kind")
    op.drop_column("users", "usage_window_reset_at")
    op.drop_column("users", "token_budget_daily")

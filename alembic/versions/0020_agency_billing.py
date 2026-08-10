"""agency billing

Phase 5.4 — per-agency billing on rec_agencies: billing_model (flat/per_seat),
subscription_status, and Stripe customer/subscription ids.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("rec_agencies")}
    if "billing_model" not in cols:
        op.add_column(
            "rec_agencies",
            sa.Column(
                "billing_model",
                sa.Enum("flat", "per_seat", name="billingmodel"),
                nullable=False,
                server_default="flat",
            ),
        )
    if "subscription_status" not in cols:
        op.add_column(
            "rec_agencies",
            sa.Column("subscription_status", sa.String(30), nullable=False, server_default="inactive"),
        )
    if "stripe_customer_id" not in cols:
        op.add_column("rec_agencies", sa.Column("stripe_customer_id", sa.String(64), nullable=True))
    if "stripe_subscription_id" not in cols:
        op.add_column("rec_agencies", sa.Column("stripe_subscription_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("rec_agencies", "stripe_subscription_id")
    op.drop_column("rec_agencies", "stripe_customer_id")
    op.drop_column("rec_agencies", "subscription_status")
    op.drop_column("rec_agencies", "billing_model")

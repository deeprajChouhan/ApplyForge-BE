"""initial

Revision ID: 0001_initial
Revises: 
Create Date: 2026-04-22
"""
from alembic import op
import sqlalchemy as sa

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.db.base import Base
    import app.models.models  # noqa
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    from app.db.base import Base
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)

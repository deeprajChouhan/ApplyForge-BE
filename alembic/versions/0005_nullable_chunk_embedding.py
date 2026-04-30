"""Make knowledge_chunks.embedding nullable (vectors moved to Qdrant)

Revision ID: 0005_nullable_chunk_embedding
Revises: 0004_userprofile_phone_age
Create Date: 2026-04-30
"""
from alembic import op
import sqlalchemy as sa

revision = '0005_nullable_chunk_embedding'
down_revision = '0004_userprofile_phone_age'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Vectors are now stored in Qdrant; the embedding column is kept for
    # backward compatibility but no longer populated by RAGService.
    op.alter_column(
        'knowledge_chunks',
        'embedding',
        existing_type=sa.Text(),
        nullable=True,
    )


def downgrade() -> None:
    # Restore NOT NULL — note: any NULL rows will cause this to fail unless
    # they are backfilled first.
    op.alter_column(
        'knowledge_chunks',
        'embedding',
        existing_type=sa.Text(),
        nullable=False,
    )

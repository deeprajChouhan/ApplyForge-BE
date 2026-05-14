"""add multi-resume support columns

Adds:
  - knowledge_documents.parsed_resume_id   FK → parsed_resume_data
  - knowledge_chunks.parsed_resume_id      FK → parsed_resume_data  (mirrors parent for fast Qdrant sync)
  - job_applications.selected_resume_id    FK → parsed_resume_data  (per-application resume selection)

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # knowledge_documents — tag documents by which resume they came from
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "parsed_resume_id",
            sa.Integer(),
            sa.ForeignKey("parsed_resume_data.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_knowledge_documents_parsed_resume_id",
        "knowledge_documents",
        ["parsed_resume_id"],
    )

    # knowledge_chunks — mirrors parent for fast DB filtering
    op.add_column(
        "knowledge_chunks",
        sa.Column(
            "parsed_resume_id",
            sa.Integer(),
            sa.ForeignKey("parsed_resume_data.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_parsed_resume_id",
        "knowledge_chunks",
        ["parsed_resume_id"],
    )

    # job_applications — which resume this job uses (NULL = latest / default)
    op.add_column(
        "job_applications",
        sa.Column(
            "selected_resume_id",
            sa.Integer(),
            sa.ForeignKey("parsed_resume_data.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_job_applications_selected_resume_id",
        "job_applications",
        ["selected_resume_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_job_applications_selected_resume_id", table_name="job_applications")
    op.drop_column("job_applications", "selected_resume_id")

    op.drop_index("ix_knowledge_chunks_parsed_resume_id", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "parsed_resume_id")

    op.drop_index("ix_knowledge_documents_parsed_resume_id", table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "parsed_resume_id")

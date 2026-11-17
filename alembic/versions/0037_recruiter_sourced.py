"""recruiter sourced stage

- rec_sourced_profiles: candidates captured (e.g. from LinkedIn) but not yet
  vetted into the pool. Holds the scraped profile, notes and the last fit
  score against the role being sourced for.
- rec_sourced_documents: CVs / other files attached before promotion.

No existing tables change. Idempotent (also created at boot by
ensure_recruiter_tables). Stays inside the rec_* data wall.

Revision ID: 0037_recruiter_sourced
Revises: 0036_recruiter_role_visibility
Create Date: 2026-11-13
"""
from alembic import op
import sqlalchemy as sa


revision = "0037_recruiter_sourced"
down_revision = "0036_recruiter_role_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("rec_sourced_profiles"):
        op.create_table(
            "rec_sourced_profiles",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("agency_id", sa.Integer, sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("role_id", sa.Integer, sa.ForeignKey("rec_roles.id", ondelete="SET NULL"), nullable=True, index=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="sourced", index=True),
            sa.Column("linkedin_url", sa.String(500), nullable=False),
            sa.Column("full_name", sa.String(200)),
            sa.Column("headline", sa.String(300)),
            sa.Column("location", sa.String(200)),
            sa.Column("about", sa.Text),
            sa.Column("email", sa.String(255)),
            sa.Column("phone", sa.String(60)),
            sa.Column("years_experience", sa.Float),
            sa.Column("skills", sa.JSON),
            sa.Column("experiences", sa.JSON),
            sa.Column("education", sa.JSON),
            sa.Column("certifications", sa.JSON),
            sa.Column("languages", sa.JSON),
            sa.Column("page_text", sa.Text),
            sa.Column("notes", sa.Text),
            sa.Column("fit_score", sa.Float),
            sa.Column("fit_reasons", sa.JSON),
            sa.Column("fit_gaps", sa.JSON),
            sa.Column("fit_breakdown", sa.JSON),
            sa.Column("scored_at", sa.DateTime),
            sa.Column("promoted_candidate_id", sa.Integer, sa.ForeignKey("rec_candidate_profiles.id", ondelete="SET NULL"), nullable=True, index=True),
            sa.Column("promoted_at", sa.DateTime),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
            sa.UniqueConstraint("agency_id", "linkedin_url", name="uq_rec_sourced_agency_url"),
        )
    if not insp.has_table("rec_sourced_documents"):
        op.create_table(
            "rec_sourced_documents",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("agency_id", sa.Integer, sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("sourced_id", sa.Integer, sa.ForeignKey("rec_sourced_profiles.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("kind", sa.String(20), nullable=False, server_default="cv"),
            sa.Column("storage_uri", sa.String(700), nullable=False),
            sa.Column("original_filename", sa.String(255), nullable=False),
            sa.Column("content_type", sa.String(120)),
            sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("extracted_text", sa.Text),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for t in ("rec_sourced_documents", "rec_sourced_profiles"):
        if insp.has_table(t):
            op.drop_table(t)

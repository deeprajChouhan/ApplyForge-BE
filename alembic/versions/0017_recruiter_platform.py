"""recruiter platform

Creates the recruiter platform schema — all rec_-prefixed tables, isolated from
the consumer schema (no foreign keys into consumer tables). Mirrors
app/recruiter/models.py.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name)


def upgrade() -> None:
    op.create_table(
        "rec_agencies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "rec_recruiters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("role", _enum("owner", "recruiter", name="recruiterseatrole"), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_rec_recruiters_agency_id", "rec_recruiters", ["agency_id"])
    op.create_index("ix_rec_recruiters_email", "rec_recruiters", ["email"], unique=True)

    op.create_table(
        "rec_clients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("industry", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_rec_clients_agency_id", "rec_clients", ["agency_id"])

    op.create_table(
        "rec_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("rec_clients.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", _enum("open", "on_hold", "filled", "closed", name="rolestatus"), nullable=True),
        sa.Column(
            "employment_type",
            _enum("full_time", "part_time", "contract", "internship", "temporary", name="employmenttype"),
            nullable=True,
        ),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("seniority", sa.String(80), nullable=True),
        sa.Column("required_skills", sa.JSON(), nullable=True),
        sa.Column("preferred_skills", sa.JSON(), nullable=True),
        sa.Column("min_years_experience", sa.Float(), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_rec_roles_agency_id", "rec_roles", ["agency_id"])

    op.create_table(
        "rec_candidate_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "source",
            _enum("bulk_cv", "manual", "ats_sync", "referral", name="candidatesource"),
            nullable=True,
        ),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(60), nullable=True),
        sa.Column("headline", sa.String(300), nullable=True),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("years_experience", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("raw_cv_text", sa.Text(), nullable=True),
        sa.Column("source_file", sa.String(500), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("provisioned_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_rec_candidate_profiles_agency_id", "rec_candidate_profiles", ["agency_id"])
    op.create_index("ix_rec_candidate_profiles_email", "rec_candidate_profiles", ["email"])

    op.create_table(
        "rec_candidate_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
    )
    op.create_index("ix_rec_candidate_skills_candidate_id", "rec_candidate_skills", ["candidate_id"])

    op.create_table(
        "rec_work_experiences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("company", sa.String(200), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_index("ix_rec_work_experiences_candidate_id", "rec_work_experiences", ["candidate_id"])

    op.create_table(
        "rec_shortlists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("rec_roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_rec_shortlists_agency_id", "rec_shortlists", ["agency_id"])
    op.create_index("ix_rec_shortlists_role_id", "rec_shortlists", ["role_id"])

    op.create_table(
        "rec_shortlist_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shortlist_id", sa.Integer(), sa.ForeignKey("rec_shortlists.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("fit_score", sa.Float(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=True),
        sa.Column("gaps", sa.JSON(), nullable=True),
        sa.Column("score_breakdown", sa.JSON(), nullable=True),
    )
    op.create_index("ix_rec_shortlist_entries_shortlist_id", "rec_shortlist_entries", ["shortlist_id"])
    op.create_index("ix_rec_shortlist_entries_candidate_id", "rec_shortlist_entries", ["candidate_id"])

    op.create_table(
        "rec_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("rec_roles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("company_name", sa.String(200), nullable=True),
        sa.Column("job_title", sa.String(200), nullable=True),
        sa.Column(
            "stage",
            _enum(
                "sourced", "submitted", "screening", "interview", "offer", "placed", "rejected",
                name="applicationstage",
            ),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_rec_applications_agency_id", "rec_applications", ["agency_id"])
    op.create_index("ix_rec_applications_candidate_id", "rec_applications", ["candidate_id"])
    op.create_index("ix_rec_applications_stage", "rec_applications", ["stage"])


def downgrade() -> None:
    for table in [
        "rec_applications",
        "rec_shortlist_entries",
        "rec_shortlists",
        "rec_work_experiences",
        "rec_candidate_skills",
        "rec_candidate_profiles",
        "rec_roles",
        "rec_clients",
        "rec_recruiters",
        "rec_agencies",
    ]:
        op.drop_table(table)

    # Drop enum types explicitly for PostgreSQL (no-op on MySQL/SQLite).
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum_name in [
            "applicationstage",
            "candidatesource",
            "employmenttype",
            "rolestatus",
            "recruiterseatrole",
        ]:
            sa.Enum(name=enum_name).drop(bind, checkfirst=True)

"""role pipeline: budget, SWOT, drafts, market snapshot

Extends the recruiter platform with:
- Role: budget_min/max/currency, is_draft, market_snapshot (JSON)
- CandidateProfile: expected_budget_min/max/currency
- Application: swot (JSON), fit_score (cached from shortlist), added_from_shortlist_id
- New table rec_market_snapshots: crawler-sourced salary & demand data per role/skill

Idempotent — safe on databases that predate Alembic tracking for the recruiter
tables (checkfirst bootstrap).

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def _has_col(insp, table: str, col: str) -> bool:
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # rec_roles ---------------------------------------------------------------
    if not _has_col(insp, "rec_roles", "budget_min"):
        op.add_column("rec_roles", sa.Column("budget_min", sa.Integer(), nullable=True))
    if not _has_col(insp, "rec_roles", "budget_max"):
        op.add_column("rec_roles", sa.Column("budget_max", sa.Integer(), nullable=True))
    if not _has_col(insp, "rec_roles", "budget_currency"):
        op.add_column(
            "rec_roles",
            sa.Column("budget_currency", sa.String(8), nullable=False, server_default="USD"),
        )
    if not _has_col(insp, "rec_roles", "is_draft"):
        op.add_column(
            "rec_roles",
            sa.Column("is_draft", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if not _has_col(insp, "rec_roles", "market_snapshot"):
        op.add_column("rec_roles", sa.Column("market_snapshot", sa.JSON(), nullable=True))
    if not _has_col(insp, "rec_roles", "notes"):
        op.add_column("rec_roles", sa.Column("notes", sa.Text(), nullable=True))

    # rec_candidate_profiles --------------------------------------------------
    if not _has_col(insp, "rec_candidate_profiles", "expected_budget_min"):
        op.add_column(
            "rec_candidate_profiles",
            sa.Column("expected_budget_min", sa.Integer(), nullable=True),
        )
    if not _has_col(insp, "rec_candidate_profiles", "expected_budget_max"):
        op.add_column(
            "rec_candidate_profiles",
            sa.Column("expected_budget_max", sa.Integer(), nullable=True),
        )
    if not _has_col(insp, "rec_candidate_profiles", "expected_budget_currency"):
        op.add_column(
            "rec_candidate_profiles",
            sa.Column(
                "expected_budget_currency",
                sa.String(8),
                nullable=False,
                server_default="USD",
            ),
        )

    # rec_applications --------------------------------------------------------
    if not _has_col(insp, "rec_applications", "swot"):
        op.add_column("rec_applications", sa.Column("swot", sa.JSON(), nullable=True))
    if not _has_col(insp, "rec_applications", "fit_score"):
        op.add_column("rec_applications", sa.Column("fit_score", sa.Float(), nullable=True))
    if not _has_col(insp, "rec_applications", "added_from_shortlist_id"):
        op.add_column(
            "rec_applications",
            sa.Column("added_from_shortlist_id", sa.Integer(), nullable=True),
        )

    # rec_market_snapshots (new) ---------------------------------------------
    if not insp.has_table("rec_market_snapshots"):
        op.create_table(
            "rec_market_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "agency_id",
                sa.Integer(),
                sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "role_id",
                sa.Integer(),
                sa.ForeignKey("rec_roles.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("query", sa.String(300), nullable=False),
            sa.Column("location", sa.String(200), nullable=True),
            sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("salary_p25", sa.Integer(), nullable=True),
            sa.Column("salary_p50", sa.Integer(), nullable=True),
            sa.Column("salary_p75", sa.Integer(), nullable=True),
            sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
            sa.Column("top_skills", sa.JSON(), nullable=True),
            sa.Column("competing_roles", sa.JSON(), nullable=True),
            sa.Column("sources", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index(
            "ix_rec_market_snapshots_agency_id", "rec_market_snapshots", ["agency_id"]
        )
        op.create_index(
            "ix_rec_market_snapshots_role_id", "rec_market_snapshots", ["role_id"]
        )


def downgrade() -> None:
    op.drop_table("rec_market_snapshots")
    for col in ("added_from_shortlist_id", "fit_score", "swot"):
        op.drop_column("rec_applications", col)
    for col in (
        "expected_budget_currency",
        "expected_budget_max",
        "expected_budget_min",
    ):
        op.drop_column("rec_candidate_profiles", col)
    for col in (
        "notes",
        "market_snapshot",
        "is_draft",
        "budget_currency",
        "budget_max",
        "budget_min",
    ):
        op.drop_column("rec_roles", col)

"""spec sheet export: agency branding + templates

Adds the Phase 1 feature 2 substrate:
- rec_agencies: logo_url, primary_color, footer_text, spec_sheet_template_id (FK)
- rec_spec_sheet_templates: new table (agency-owned CV/spec-sheet templates
  with per-template branding overrides + anonymise_by_default toggle)

Idempotent — safe on databases that predate Alembic tracking for the
recruiter tables (checkfirst bootstrap).

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-11
"""
from alembic import op
import sqlalchemy as sa


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def _has_col(insp, table: str, col: str) -> bool:
    return col in {c["name"] for c in insp.get_columns(table)}


def _has_index(insp, table: str, name: str) -> bool:
    return name in {ix["name"] for ix in insp.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # ── rec_spec_sheet_templates (new) ─────────────────────────────────
    # Create first so the Agency FK below has something to point at.
    if not insp.has_table("rec_spec_sheet_templates"):
        op.create_table(
            "rec_spec_sheet_templates",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "agency_id",
                sa.Integer(),
                sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("logo_url", sa.String(500), nullable=True),
            sa.Column("primary_color", sa.String(9), nullable=True),
            sa.Column("header_text", sa.String(300), nullable=True),
            sa.Column("footer_text", sa.String(500), nullable=True),
            sa.Column("body_intro", sa.Text(), nullable=True),
            sa.Column(
                "anonymise_by_default",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_rec_spec_sheet_templates_agency_id",
            "rec_spec_sheet_templates",
            ["agency_id"],
        )

    # ── rec_agencies: branding columns ─────────────────────────────────
    if not _has_col(insp, "rec_agencies", "logo_url"):
        op.add_column("rec_agencies", sa.Column("logo_url", sa.String(500), nullable=True))
    if not _has_col(insp, "rec_agencies", "primary_color"):
        op.add_column("rec_agencies", sa.Column("primary_color", sa.String(9), nullable=True))
    if not _has_col(insp, "rec_agencies", "footer_text"):
        op.add_column("rec_agencies", sa.Column("footer_text", sa.String(500), nullable=True))
    if not _has_col(insp, "rec_agencies", "spec_sheet_template_id"):
        # Nullable FK; SET NULL on delete so removing a template doesn't
        # cascade into the tenant row.
        op.add_column(
            "rec_agencies",
            sa.Column("spec_sheet_template_id", sa.Integer(), nullable=True),
        )
        # Batch-create the FK for SQLite compatibility.
        with op.batch_alter_table("rec_agencies") as batch_op:
            batch_op.create_foreign_key(
                "fk_rec_agencies_spec_sheet_template",
                "rec_spec_sheet_templates",
                ["spec_sheet_template_id"],
                ["id"],
                ondelete="SET NULL",
            )
    if not _has_index(insp, "rec_agencies", "ix_rec_agencies_spec_sheet_template_id"):
        op.create_index(
            "ix_rec_agencies_spec_sheet_template_id",
            "rec_agencies",
            ["spec_sheet_template_id"],
        )


def downgrade() -> None:
    # Drop the FK from rec_agencies first, then the branding columns, then
    # the templates table.
    with op.batch_alter_table("rec_agencies") as batch_op:
        try:
            batch_op.drop_constraint("fk_rec_agencies_spec_sheet_template", type_="foreignkey")
        except Exception:
            pass
    op.drop_index("ix_rec_agencies_spec_sheet_template_id", table_name="rec_agencies")
    for col in ("spec_sheet_template_id", "footer_text", "primary_color", "logo_url"):
        try:
            op.drop_column("rec_agencies", col)
        except Exception:
            pass
    op.drop_table("rec_spec_sheet_templates")

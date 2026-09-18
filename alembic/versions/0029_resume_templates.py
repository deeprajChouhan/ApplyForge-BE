"""resume templates: first-class ResumeTemplate + selection FKs

Adds:
- resume_templates: system + user-owned resume template configs
  (config_json describes section ordering, labels, styles).
- user_profiles.default_resume_template_id: user account default.
- job_applications.resume_template_id: per-application selection.
- generated_documents.resume_template_id: snapshot at generation time.

Also seeds the four system templates (classic, modern, sidebar, executive)
that used to live only as frontend-only React components.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa
import json


revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


# Kept in sync with app.services.templates.defaults.SYSTEM_TEMPLATES.
# Duplicated here so the migration is self-contained (Alembic downgrades
# should never import live app modules whose schemas may have moved on).
_DEFAULT_SECTIONS = [
    {"key": "header",         "label": "Header",                "enabled": True, "order": 0},
    {"key": "summary",        "label": "Professional Summary",  "enabled": True, "order": 1},
    {"key": "skills",         "label": "Skills",                "enabled": True, "order": 2},
    {"key": "experience",     "label": "Work Experience",       "enabled": True, "order": 3},
    {"key": "projects",       "label": "Projects",              "enabled": True, "order": 4},
    {"key": "education",      "label": "Education",             "enabled": True, "order": 5},
    {"key": "certifications", "label": "Certifications",        "enabled": True, "order": 6},
]

def _cfg(layout, styles_override=None, sections_override=None):
    styles = {
        "font_family": "Helvetica",
        "body_font_size": 10,
        "heading_font_size": 12,
        "line_height": 1.35,
        "section_spacing": 8,
        "margin_top": 40,
        "margin_bottom": 40,
        "margin_left": 46,
        "margin_right": 46,
        "accent_color": "#1D4ED8",
        "header_style": "left",
    }
    if styles_override:
        styles.update(styles_override)
    sections = sections_override or _DEFAULT_SECTIONS
    return {"version": 1, "layout": layout, "sections": sections, "styles": styles}


_SYSTEM_SEED = [
    ("Classic ATS",         "classic",   _cfg("single_column")),
    ("Modern",              "modern",    _cfg("single_column", {"accent_color": "#0F766E", "font_family": "Inter"})),
    ("Two-Column Sidebar",  "sidebar",   _cfg("two_column", {"accent_color": "#1E3A5F"})),
    ("Executive",           "executive", _cfg("centered", {"font_family": "Georgia", "accent_color": "#0F172A", "heading_font_size": 13})),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("resume_templates"):
        op.create_table(
            "resume_templates",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("base_template", sa.String(40), nullable=False),
            sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("config_json", sa.Text(), nullable=False),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_resume_templates_user_id", "resume_templates", ["user_id"])
        op.create_index("ix_resume_templates_base_template", "resume_templates", ["base_template"])
        op.create_index("ix_resume_templates_user_system", "resume_templates", ["user_id", "is_system"])
        op.create_index("ix_resume_templates_deleted_at", "resume_templates", ["deleted_at"])

    # Seed system templates (idempotent — skip if any is_system row already exists).
    seeded = bind.execute(sa.text(
        "SELECT COUNT(*) FROM resume_templates WHERE is_system = 1"
    )).scalar() or 0
    if seeded == 0:
        for name, base, cfg in _SYSTEM_SEED:
            bind.execute(sa.text(
                "INSERT INTO resume_templates "
                "(user_id, name, base_template, is_system, config_json) "
                "VALUES (NULL, :name, :base, 1, :cfg)"
            ), {"name": name, "base": base, "cfg": json.dumps(cfg)})

    # user_profiles.default_resume_template_id
    if "default_resume_template_id" not in {c["name"] for c in insp.get_columns("user_profiles")}:
        with op.batch_alter_table("user_profiles") as bop:
            bop.add_column(sa.Column(
                "default_resume_template_id", sa.Integer(),
                sa.ForeignKey("resume_templates.id", ondelete="SET NULL"),
                nullable=True,
            ))
        op.create_index(
            "ix_user_profiles_default_resume_template_id",
            "user_profiles", ["default_resume_template_id"],
        )

    # job_applications.resume_template_id
    if "resume_template_id" not in {c["name"] for c in insp.get_columns("job_applications")}:
        with op.batch_alter_table("job_applications") as bop:
            bop.add_column(sa.Column(
                "resume_template_id", sa.Integer(),
                sa.ForeignKey("resume_templates.id", ondelete="SET NULL"),
                nullable=True,
            ))
        op.create_index(
            "ix_job_applications_resume_template_id",
            "job_applications", ["resume_template_id"],
        )

    # generated_documents.resume_template_id
    if "resume_template_id" not in {c["name"] for c in insp.get_columns("generated_documents")}:
        with op.batch_alter_table("generated_documents") as bop:
            bop.add_column(sa.Column(
                "resume_template_id", sa.Integer(),
                sa.ForeignKey("resume_templates.id", ondelete="SET NULL"),
                nullable=True,
            ))
        op.create_index(
            "ix_generated_documents_resume_template_id",
            "generated_documents", ["resume_template_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    for tbl, col, ix in [
        ("generated_documents", "resume_template_id", "ix_generated_documents_resume_template_id"),
        ("job_applications",    "resume_template_id", "ix_job_applications_resume_template_id"),
        ("user_profiles",       "default_resume_template_id", "ix_user_profiles_default_resume_template_id"),
    ]:
        cols = {c["name"] for c in insp.get_columns(tbl)}
        if col in cols:
            try:
                op.drop_index(ix, table_name=tbl)
            except Exception:
                pass
            with op.batch_alter_table(tbl) as bop:
                bop.drop_column(col)

    if insp.has_table("resume_templates"):
        op.drop_table("resume_templates")

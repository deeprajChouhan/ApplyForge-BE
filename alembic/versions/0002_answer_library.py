"""add answer_library and application_kits tables

Revision ID: 0002_answer_library
Revises: 0001_auto_apply_core
Create Date: 2026-08-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_answer_library"
down_revision = "0001_auto_apply_core"
branch_labels = None
depends_on = None


field_type_enum = sa.Enum(
    "short_text",
    "long_text",
    "single_select",
    "multi_select",
    "boolean",
    "number",
    "date",
    "email",
    "phone",
    "url",
    "file",
    "eeoc",
    name="field_type_enum",
)


def upgrade() -> None:
    # --- answer_library ---
    op.create_table(
        "answer_library",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("question_key", sa.String(length=64), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("question_embedding_json", sa.JSON(), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column(
            "field_type",
            field_type_enum,
            nullable=False,
            server_default="short_text",
        ),
        sa.Column("tags", sa.String(length=512), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "source", sa.String(length=32), nullable=False, server_default="user"
        ),
        sa.Column(
            "times_used", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "user_id", "question_key", name="uq_answer_library_user_question"
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_answer_library_user_id", "answer_library", ["user_id"], unique=False
    )
    op.create_index(
        "ix_answer_library_user_field",
        "answer_library",
        ["user_id", "field_type"],
        unique=False,
    )

    # --- application_kits ---
    op.create_table(
        "application_kits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("base_resume_id", sa.Integer(), nullable=True),
        sa.Column(
            "tone",
            sa.String(length=32),
            nullable=False,
            server_default="professional",
        ),
        sa.Column(
            "cover_letter_style",
            sa.String(length=32),
            nullable=False,
            server_default="standard",
        ),
        sa.Column("default_answers_ref_json", sa.JSON(), nullable=True),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        # base_resume_id is a soft reference (no FK) — resumes live in
        # `uploaded_files` in this codebase; validity is enforced at the
        # service layer instead.
        sa.UniqueConstraint(
            "user_id", "name", name="uq_application_kits_user_name"
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_application_kits_user_id", "application_kits", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_application_kits_user_id", table_name="application_kits")
    op.drop_table("application_kits")

    op.drop_index("ix_answer_library_user_field", table_name="answer_library")
    op.drop_index("ix_answer_library_user_id", table_name="answer_library")
    op.drop_table("answer_library")

    field_type_enum.drop(op.get_bind(), checkfirst=True)

"""add evaluation infrastructure tables

Adds:
  - llm_usage_logs        (cost/latency tracking per LLM call)
  - application_evaluations (scorer + hallucination + outcome per application)

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-11
"""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── llm_usage_logs ────────────────────────────────────────────────────────
    op.create_table(
        "llm_usage_logs",
        sa.Column("id",                 sa.Integer(),     nullable=False, autoincrement=True),
        sa.Column("application_id",     sa.Integer(),     nullable=True),
        sa.Column("user_id",            sa.Integer(),     nullable=True),
        sa.Column("model_name",         sa.String(100),   nullable=False),
        sa.Column("operation",          sa.String(100),   nullable=False, server_default="generate"),
        sa.Column("prompt_tokens",      sa.Integer(),     nullable=False, server_default="0"),
        sa.Column("completion_tokens",  sa.Integer(),     nullable=False, server_default="0"),
        sa.Column("total_tokens",       sa.Integer(),     nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Float(),       nullable=False, server_default="0.0"),
        sa.Column("latency_ms",         sa.Float(),       nullable=False, server_default="0.0"),
        sa.Column("called_at",          sa.DateTime(),    nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["application_id"], ["job_applications.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"],         ["users.id"],           ondelete="SET NULL"),
    )
    op.create_index("idx_llm_logs_user_date", "llm_usage_logs", ["user_id", "called_at"])
    op.create_index("idx_llm_logs_app",       "llm_usage_logs", ["application_id"])

    # ── application_evaluations ───────────────────────────────────────────────
    op.create_table(
        "application_evaluations",
        sa.Column("id",                       sa.Integer(),  nullable=False, autoincrement=True),
        sa.Column("application_id",           sa.Integer(),  nullable=False),
        sa.Column("user_id",                  sa.Integer(),  nullable=True),
        sa.Column("doc_type",                 sa.String(50), nullable=False, server_default="cover_letter"),
        sa.Column("ats_keyword_match",        sa.Float(),    nullable=True),
        sa.Column("tone_score",               sa.Float(),    nullable=True),
        sa.Column("length_score",             sa.Float(),    nullable=True),
        sa.Column("experience_relevance",     sa.Float(),    nullable=True),
        sa.Column("overall_score",            sa.Float(),    nullable=True),
        sa.Column("score_method",             sa.String(20), nullable=True),
        sa.Column("score_reasoning_json",     sa.Text(),     nullable=True),
        sa.Column("hallucination_flags_json", sa.Text(),     nullable=True),
        sa.Column("hallucination_count",      sa.Integer(),  nullable=False, server_default="0"),
        sa.Column("outcome",                  sa.String(20), nullable=True),
        sa.Column("evaluated_at",             sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["application_id"], ["job_applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"],         ["users.id"],           ondelete="SET NULL"),
    )
    op.create_index("idx_eval_app_doc", "application_evaluations", ["application_id", "doc_type"])
    op.create_index("idx_eval_outcome", "application_evaluations", ["outcome"])


def downgrade() -> None:
    op.drop_index("idx_eval_outcome",  "application_evaluations")
    op.drop_index("idx_eval_app_doc",  "application_evaluations")
    op.drop_table("application_evaluations")

    op.drop_index("idx_llm_logs_app",       "llm_usage_logs")
    op.drop_index("idx_llm_logs_user_date", "llm_usage_logs")
    op.drop_table("llm_usage_logs")

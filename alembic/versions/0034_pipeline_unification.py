"""pipeline unification: unique app per role+candidate, interview tz, per-interviewer feedback

- Merges duplicate rec_applications rows (same role_id + candidate_id) into the
  oldest one, re-pointing child rows (notes, submissions, interviews, offers)
  and keeping the most advanced stage, then adds a unique index so the same
  candidate can never appear twice on a role's board / shortlist.
- rec_interviews.timezone (IANA zone the slot was scheduled in).
- rec_interview_feedback.interviewer_email (one feedback row per interviewer).

Idempotent.

Revision ID: 0034_pipeline_unification
Revises: 0033_client_share_tokens
Create Date: 2026-11-02
"""
from alembic import op
import sqlalchemy as sa


revision = "0034_pipeline_unification"
down_revision = "0033_client_share_tokens"
branch_labels = None
depends_on = None

_STAGE_RANK = {
    "sourced": 0, "screening": 1, "submitted": 2, "interview": 3,
    "offer": 4, "placed": 5, "rejected": -1,
}
_CHILD_TABLES = (
    "rec_application_notes",
    "rec_client_submissions",
    "rec_interviews",
    "rec_offers",
)


def _cols(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("rec_interviews") and "timezone" not in _cols(insp, "rec_interviews"):
        op.add_column("rec_interviews", sa.Column("timezone", sa.String(64), nullable=True))

    if (
        insp.has_table("rec_interview_feedback")
        and "interviewer_email" not in _cols(insp, "rec_interview_feedback")
    ):
        op.add_column(
            "rec_interview_feedback",
            sa.Column("interviewer_email", sa.String(255), nullable=True),
        )

    if not insp.has_table("rec_applications"):
        return

    # ── Merge duplicates ────────────────────────────────────────────────
    dupes = bind.execute(sa.text(
        "SELECT role_id, candidate_id FROM rec_applications "
        "WHERE role_id IS NOT NULL GROUP BY role_id, candidate_id HAVING COUNT(*) > 1"
    )).fetchall()
    existing_tables = set(insp.get_table_names())
    for role_id, candidate_id in dupes:
        rows = bind.execute(
            sa.text(
                "SELECT id, stage FROM rec_applications "
                "WHERE role_id = :r AND candidate_id = :c ORDER BY id"
            ),
            {"r": role_id, "c": candidate_id},
        ).fetchall()
        keep_id = rows[0][0]
        best_stage = max((r[1] for r in rows), key=lambda st: _STAGE_RANK.get(str(st), 0))
        drop_ids = [r[0] for r in rows[1:]]
        for child in _CHILD_TABLES:
            if child not in existing_tables:
                continue
            for did in drop_ids:
                bind.execute(
                    sa.text(f"UPDATE {child} SET application_id = :k WHERE application_id = :d"),
                    {"k": keep_id, "d": did},
                )
        bind.execute(
            sa.text("UPDATE rec_applications SET stage = :s WHERE id = :k"),
            {"s": best_stage, "k": keep_id},
        )
        for did in drop_ids:
            bind.execute(sa.text("DELETE FROM rec_applications WHERE id = :d"), {"d": did})

    existing_idx = {i["name"] for i in insp.get_indexes("rec_applications")}
    existing_uq = {u["name"] for u in insp.get_unique_constraints("rec_applications")}
    if "uq_rec_app_role_candidate" not in existing_idx | existing_uq:
        with op.batch_alter_table("rec_applications") as batch:
            batch.create_unique_constraint(
                "uq_rec_app_role_candidate", ["role_id", "candidate_id"]
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("rec_applications"):
        names = {u["name"] for u in insp.get_unique_constraints("rec_applications")}
        if "uq_rec_app_role_candidate" in names:
            with op.batch_alter_table("rec_applications") as batch:
                batch.drop_constraint("uq_rec_app_role_candidate", type_="unique")
    if insp.has_table("rec_interview_feedback") and "interviewer_email" in _cols(insp, "rec_interview_feedback"):
        op.drop_column("rec_interview_feedback", "interviewer_email")
    if insp.has_table("rec_interviews") and "timezone" in _cols(insp, "rec_interviews"):
        op.drop_column("rec_interviews", "timezone")

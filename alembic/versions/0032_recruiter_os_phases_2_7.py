"""Recruiter OS Phases 2-7: consent, submissions, feedback, interviews,
offers, placements, tasks, notifications, AI insight cache.

All additive. No touches to Phase-1 tables beyond adding indexes.

Revision ID: 0032_recruiter_os_phases_2_7
Revises: 0031_candidate_role_screening
Create Date: 2026-09-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0032_recruiter_os_phases_2_7"
down_revision = "0031_candidate_role_screening"
branch_labels = None
depends_on = None


# ── Enum type helpers (Postgres creates types up-front; MySQL/SQLite inline) ──
CONSENT_STATUS = sa.Enum("pending", "confirmed", "declined", "expired", name="consentstatus")
CONSENT_METHOD = sa.Enum("email", "phone", "portal", "manual", name="consentmethod")
SUBMISSION_STATUS = sa.Enum(
    "draft", "submitted", "client_reviewing", "progressed", "on_hold", "rejected", "withdrawn",
    name="submissionstatus",
)
CLIENT_DECISION = sa.Enum("progress", "hold", "reject", name="clientdecision")
INTERVIEW_STAGE = sa.Enum("screen", "first", "second", "final", "other", name="interviewstage")
INTERVIEW_TYPE = sa.Enum("phone", "video", "onsite", "technical", "panel", name="interviewtype")
INTERVIEW_STATUS = sa.Enum(
    "scheduled", "completed", "cancelled", "rescheduled", "no_show", name="interviewstatus"
)
OFFER_STATUS = sa.Enum(
    "draft", "sent", "negotiating", "accepted", "declined", "withdrawn", name="offerstatus"
)
PLACEMENT_STATUS = sa.Enum(
    "upcoming", "started", "completed", "cancelled", "refunded", "replaced", name="placementstatus"
)
TASK_STATUS = sa.Enum("open", "in_progress", "done", "dismissed", name="taskstatus")
TASK_PRIORITY = sa.Enum("low", "medium", "high", "urgent", name="taskpriority")
NOTIFICATION_KIND = sa.Enum(
    "candidate_screening_due", "consent_missing", "submission_viewed",
    "client_feedback_received", "feedback_overdue", "interview_requested",
    "interview_upcoming", "interview_feedback_missing", "offer_updated",
    "offer_expiring", "candidate_accepted", "candidate_declined",
    "start_date_upcoming", "guarantee_ending", "stale_pipeline", "other",
    name="notificationkind",
)

_ALL_ENUMS = [
    CONSENT_STATUS, CONSENT_METHOD, SUBMISSION_STATUS, CLIENT_DECISION,
    INTERVIEW_STAGE, INTERVIEW_TYPE, INTERVIEW_STATUS, OFFER_STATUS,
    PLACEMENT_STATUS, TASK_STATUS, TASK_PRIORITY, NOTIFICATION_KIND,
]


def upgrade() -> None:
    bind = op.get_bind()
    for e in _ALL_ENUMS:
        e.create(bind, checkfirst=True)

    # ── Phase 2: Consent + ClientSubmission ─────────────────────────────
    op.create_table(
        "rec_candidate_consents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("rec_roles.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("status", CONSENT_STATUS, nullable=False, server_default="pending"),
        sa.Column("method", CONSENT_METHOD, nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.Column("captured_by", sa.Integer(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_rec_consent_cand_role", "rec_candidate_consents", ["candidate_id", "role_id"])

    op.create_table(
        "rec_client_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("rec_clients.id", ondelete="SET NULL"), nullable=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("rec_roles.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("rec_applications.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("submitted_by", sa.Integer(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("status", SUBMISSION_STATUS, nullable=False, server_default="draft"),
        sa.Column("client_summary", sa.Text(), nullable=True),
        sa.Column("key_strengths", sa.JSON(), nullable=True),
        sa.Column("potential_gaps", sa.JSON(), nullable=True),
        sa.Column("compensation_snapshot", sa.JSON(), nullable=True),
        sa.Column("notice_period_snapshot", sa.JSON(), nullable=True),
        sa.Column("availability_snapshot", sa.JSON(), nullable=True),
        sa.Column("motivation_snapshot", sa.Text(), nullable=True),
        sa.Column("cv_version_id", sa.String(120), nullable=True),
        sa.Column("client_decision", CLIENT_DECISION, nullable=True),
        sa.Column("client_feedback", sa.Text(), nullable=True),
        sa.Column("client_viewed_at", sa.DateTime(), nullable=True),
        sa.Column("client_responded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # ── Phase 3: SubmissionFeedback + SLA ──────────────────────────────
    op.create_table(
        "rec_submission_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("rec_client_submissions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("decision", CLIENT_DECISION, nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("client_contact_name", sa.String(200), nullable=True),
        sa.Column("client_contact_email", sa.String(255), nullable=True),
        sa.Column("submitted_via", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "rec_client_sla_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("rec_clients.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("expected_feedback_hours", sa.Integer(), nullable=False, server_default="48"),
        sa.Column("notify_recruiter", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # ── Phase 4: Interviews ────────────────────────────────────────────
    op.create_table(
        "rec_interviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("rec_applications.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("rec_client_submissions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("rec_roles.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("rec_clients.id", ondelete="SET NULL"), nullable=True),
        sa.Column("stage", INTERVIEW_STAGE, nullable=False, server_default="first"),
        sa.Column("interview_type", INTERVIEW_TYPE, nullable=True),
        sa.Column("interviewers", sa.JSON(), nullable=True),
        sa.Column("proposed_times", sa.JSON(), nullable=True),
        sa.Column("confirmed_time", sa.DateTime(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("location", sa.String(300), nullable=True),
        sa.Column("meeting_url", sa.String(500), nullable=True),
        sa.Column("status", INTERVIEW_STATUS, nullable=False, server_default="scheduled"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "rec_interview_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("interview_id", sa.Integer(), sa.ForeignKey("rec_interviews.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("interviewer_name", sa.String(200), nullable=True),
        sa.Column("technical", sa.Integer(), nullable=True),
        sa.Column("communication", sa.Integer(), nullable=True),
        sa.Column("role_understanding", sa.Integer(), nullable=True),
        sa.Column("domain_knowledge", sa.Integer(), nullable=True),
        sa.Column("leadership", sa.Integer(), nullable=True),
        sa.Column("culture_alignment", sa.Integer(), nullable=True),
        sa.Column("decision", CLIENT_DECISION, nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # ── Phase 5: Offers + Placements ──────────────────────────────────
    op.create_table(
        "rec_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("rec_applications.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("rec_client_submissions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("rec_roles.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("rec_clients.id", ondelete="SET NULL"), nullable=True),
        sa.Column("base_compensation", sa.JSON(), nullable=True),
        sa.Column("bonus", sa.JSON(), nullable=True),
        sa.Column("equity", sa.JSON(), nullable=True),
        sa.Column("allowances", sa.JSON(), nullable=True),
        sa.Column("benefits", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("offer_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("status", OFFER_STATUS, nullable=False, server_default="draft"),
        sa.Column("assigned_recruiter_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "rec_offer_negotiations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("offer_id", sa.Integer(), sa.ForeignKey("rec_offers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("round_label", sa.String(60), server_default="counter"),
        sa.Column("from_party", sa.String(20), nullable=True),
        sa.Column("compensation", sa.JSON(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("author_recruiter_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "rec_placements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("offer_id", sa.Integer(), sa.ForeignKey("rec_offers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("rec_roles.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("rec_clients.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recruiter_id", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("final_compensation", sa.JSON(), nullable=True),
        sa.Column("fee_percent", sa.Float(), nullable=True),
        sa.Column("fee_amount", sa.Integer(), nullable=True),
        sa.Column("guarantee_weeks", sa.Integer(), nullable=True),
        sa.Column("guarantee_ends_at", sa.Date(), nullable=True),
        sa.Column("status", PLACEMENT_STATUS, nullable=False, server_default="upcoming"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "rec_post_placement_checkins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("placement_id", sa.Integer(), sa.ForeignKey("rec_placements.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("day_offset", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_by", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(40), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    # ── Phase 6: Tasks + Notifications ───────────────────────────────
    op.create_table(
        "rec_recruiter_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("owner_recruiter_id", sa.Integer(), nullable=True, index=True),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("status", TASK_STATUS, nullable=False, server_default="open"),
        sa.Column("priority", TASK_PRIORITY, nullable=False, server_default="medium"),
        sa.Column("due_at", sa.DateTime(), nullable=True, index=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("parent_kind", sa.String(30), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True, index=True),
        sa.Column("ai_generated", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        "rec_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("recruiter_id", sa.Integer(), nullable=True, index=True),
        sa.Column("kind", NOTIFICATION_KIND, nullable=False, index=True),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("parent_kind", sa.String(30), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True, index=True),
        sa.Column("priority", TASK_PRIORITY, nullable=False, server_default="medium"),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), index=True),
    )

    # ── Phase 7: AI insight cache ────────────────────────────────────
    op.create_table(
        "rec_ai_insight_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agency_id", sa.Integer(), sa.ForeignKey("rec_agencies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("scope", sa.String(40), nullable=False, index=True),
        sa.Column("scope_id", sa.Integer(), nullable=True, index=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("used_llm", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    for t in (
        "rec_ai_insight_cache",
        "rec_notifications",
        "rec_recruiter_tasks",
        "rec_post_placement_checkins",
        "rec_placements",
        "rec_offer_negotiations",
        "rec_offers",
        "rec_interview_feedback",
        "rec_interviews",
        "rec_client_sla_configs",
        "rec_submission_feedback",
        "rec_client_submissions",
        "rec_candidate_consents",
    ):
        op.drop_table(t)
    bind = op.get_bind()
    for e in reversed(_ALL_ENUMS):
        e.drop(bind, checkfirst=True)

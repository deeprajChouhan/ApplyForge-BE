"""
Recruiter platform data model (Phase 1).

Shares the app's declarative Base but uses `rec_`-prefixed tables and holds NO
foreign keys into consumer tables — that's the data wall. Every entity is
agency-scoped via `agency_id`; candidate pools never mix across agencies, and
nothing here references the consumer schema.

Class names are chosen to avoid collisions with consumer models registered on
the same Base (e.g. the recruiter "work experience" is `CandidateExperience`,
since the consumer app already defines `WorkExperience`).
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.recruiter.enums import (
    AgencyPlan,
    AgencyStatus,
    ApplicationStage,
    BillingModel,
    CandidateSource,
    ClientDecision,
    CompensationPeriod,
    ConsentMethod,
    ConsentStatus,
    EmploymentType,
    FeedbackReason,
    InterviewStage,
    InterviewStatus,
    InterviewType,
    MotivationCategory,
    NoticeUnit,
    NotificationKind,
    OfferStatus,
    PlacementStatus,
    RecruiterSeatRole,
    RelocationPreference,
    RoleStatus,
    ScreeningOutcome,
    SubmissionStatus,
    TaskPriority,
    TaskStatus,
    WorkModel,
)


class RecTimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Agency(Base, RecTimestampMixin):
    __tablename__ = "rec_agencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)

    # Billing tier (Phase 5). seat_limit NULL = unlimited; when NULL for a set
    # plan the effective limit falls back to the plan default.
    plan: Mapped[AgencyPlan] = mapped_column(
        SAEnum(AgencyPlan), default=AgencyPlan.free, nullable=False, server_default=AgencyPlan.free.value
    )
    seat_limit: Mapped[int | None] = mapped_column(Integer)

    # Billing (Phase 5.4). billing_model is chosen per agency by the operator.
    billing_model: Mapped[BillingModel] = mapped_column(
        SAEnum(BillingModel), default=BillingModel.flat, nullable=False, server_default=BillingModel.flat.value
    )
    subscription_status: Mapped[str] = mapped_column(
        String(30), default="inactive", nullable=False, server_default="inactive"
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64))
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64))

    # Onboarding lifecycle (Phase 5.5). Operator-created agencies default to
    # active with no trial; self-serve signups start pending with a trial clock.
    status: Mapped[AgencyStatus] = mapped_column(
        SAEnum(AgencyStatus), default=AgencyStatus.active, nullable=False, server_default=AgencyStatus.active.value
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Branding — applied to exported CV/spec-sheet documents (Phase 1 feature 2).
    # logo_url: URL of an agency-hosted PNG/JPG (we embed it in exports; the
    #   agency uploads/hosts it themselves rather than us running an asset store).
    # primary_color: hex like "#0f766e" — used for headings + accent bars.
    # footer_text: one-line legal/contact footer printed on every export.
    # spec_sheet_template_id: default SpecSheetTemplate for this agency, if any;
    #   the export endpoint falls back to agency defaults when no template is set.
    logo_url: Mapped[str | None] = mapped_column(String(500))
    primary_color: Mapped[str | None] = mapped_column(String(9))
    footer_text: Mapped[str | None] = mapped_column(String(500))
    spec_sheet_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("rec_spec_sheet_templates.id", ondelete="SET NULL"), index=True
    )

    recruiters: Mapped[list["Recruiter"]] = relationship(
        back_populates="agency", cascade="all, delete-orphan"
    )
    clients: Mapped[list["Client"]] = relationship(
        back_populates="agency", cascade="all, delete-orphan"
    )
    roles: Mapped[list["Role"]] = relationship(
        back_populates="agency", cascade="all, delete-orphan"
    )
    candidates: Mapped[list["CandidateProfile"]] = relationship(
        back_populates="agency", cascade="all, delete-orphan"
    )


class Recruiter(Base, RecTimestampMixin):
    """
    A recruiter seat with its own login. Credentials live here (not in the
    consumer User table) so recruiter identities stay inside the recruiter
    module's data wall. Operators provision these accounts from the admin panel.
    """
    __tablename__ = "rec_recruiters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[RecruiterSeatRole] = mapped_column(
        SAEnum(RecruiterSeatRole), default=RecruiterSeatRole.recruiter, nullable=False
    )
    # Login credentials (bcrypt via the app's shared security helpers).
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    agency: Mapped["Agency"] = relationship(back_populates="recruiters")


class Client(Base, RecTimestampMixin):
    """A hiring company the agency serves."""
    __tablename__ = "rec_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(120))

    # Relationship contact block — a single primary contact is enough for now;
    # add a `ClientContact` child table if a client outgrows one point of contact.
    primary_contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(60))
    website: Mapped[str | None] = mapped_column(String(300))
    address: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)

    agency: Mapped["Agency"] = relationship(back_populates="clients")
    roles: Mapped[list["Role"]] = relationship(back_populates="client")


class Role(Base, RecTimestampMixin):
    """An open position the agency is filling."""
    __tablename__ = "rec_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("rec_clients.id", ondelete="SET NULL"))

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[RoleStatus] = mapped_column(SAEnum(RoleStatus), default=RoleStatus.open)
    employment_type: Mapped[EmploymentType | None] = mapped_column(SAEnum(EmploymentType))
    location: Mapped[str | None] = mapped_column(String(200))
    seniority: Mapped[str | None] = mapped_column(String(80))

    # Country the role is based in (ISO 3166-1 alpha-2). Drives MarketConfig
    # lookup for compensation/notice/employment-type schemas in the UI.
    country_code: Mapped[str | None] = mapped_column(String(2), index=True)

    required_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    min_years_experience: Mapped[float | None] = mapped_column(Float)
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)

    # Budget the client has committed for this hire (distinct from salary range,
    # which is what we publish to candidates). Used for margin and reconciliation
    # against candidate expected budgets.
    budget_min: Mapped[int | None] = mapped_column(Integer)
    budget_max: Mapped[int | None] = mapped_column(Integer)
    budget_currency: Mapped[str] = mapped_column(String(8), default="USD", server_default="USD", nullable=False)

    # Drafts don't show up in active pipeline aggregates and can be shared with
    # the client for feedback before publishing.
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    market_snapshot: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)

    embedding: Mapped[list[float] | None] = mapped_column(JSON)

    agency: Mapped["Agency"] = relationship(back_populates="roles")
    client: Mapped["Client | None"] = relationship(back_populates="roles")
    shortlists: Mapped[list["Shortlist"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )


class CandidateProfile(Base, RecTimestampMixin):
    """
    Agency-owned CRM record with no login. Deliberately NOT an ApplyForge user
    until promoted through the provisioning bridge (a later phase).
    """
    __tablename__ = "rec_candidate_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    source: Mapped[CandidateSource] = mapped_column(
        SAEnum(CandidateSource), default=CandidateSource.bulk_cv
    )

    full_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(60))
    headline: Mapped[str | None] = mapped_column(String(300))
    location: Mapped[str | None] = mapped_column(String(200))
    years_experience: Mapped[float | None] = mapped_column(Float)
    summary: Mapped[str | None] = mapped_column(Text)

    # Canonical public URL of the source profile (linkedin.com/in/<slug>).
    # Set for source=linkedin captures; used as the per-agency dedup key so the
    # same profile clicked twice updates in place rather than creating a duplicate.
    linkedin_url: Mapped[str | None] = mapped_column(String(500), index=True)

    raw_cv_text: Mapped[str | None] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(String(500))
    embedding: Mapped[list[float] | None] = mapped_column(JSON)

    # Candidate-declared expectations. Populated during ingestion (LLM parse)
    # or edited manually in the profile drawer; consumed by role-match margin.
    expected_budget_min: Mapped[int | None] = mapped_column(Integer)
    expected_budget_max: Mapped[int | None] = mapped_column(Integer)
    expected_budget_currency: Mapped[str] = mapped_column(
        String(8), default="USD", server_default="USD", nullable=False
    )

    # Optional per-country compensation preferences the recruiter captures on
    # the candidate's global profile. Used to pre-populate role-specific
    # screening compensation when a role is in that country. Shape:
    #   {"GB": {"amount": 95000, "currency": "GBP", "period": "YEAR"}, ...}
    market_preferences: Mapped[dict | None] = mapped_column(JSON)

    # Set once converted to a real ApplyForge user (provisioning bridge).
    provisioned_user_id: Mapped[int | None] = mapped_column(Integer)

    agency: Mapped["Agency"] = relationship(back_populates="candidates")
    skills: Mapped[list["CandidateSkill"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    experiences: Mapped[list["CandidateExperience"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class CandidateSkill(Base):
    __tablename__ = "rec_candidate_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)  # normalised, lowercase

    candidate: Mapped["CandidateProfile"] = relationship(back_populates="skills")


class CandidateExperience(Base):
    __tablename__ = "rec_work_experiences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(200))
    company: Mapped[str | None] = mapped_column(String(200))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)

    candidate: Mapped["CandidateProfile"] = relationship(back_populates="experiences")


class Shortlist(Base, RecTimestampMixin):
    """A saved matching run: the ranked candidates for one role at one moment."""
    __tablename__ = "rec_shortlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("rec_roles.id", ondelete="CASCADE"), index=True)

    role: Mapped["Role"] = relationship(back_populates="shortlists")
    entries: Mapped[list["ShortlistEntry"]] = relationship(
        back_populates="shortlist",
        cascade="all, delete-orphan",
        order_by="ShortlistEntry.rank",
    )


class ShortlistEntry(Base):
    __tablename__ = "rec_shortlist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shortlist_id: Mapped[int] = mapped_column(
        ForeignKey("rec_shortlists.id", ondelete="CASCADE"), index=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"), index=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    fit_score: Mapped[float] = mapped_column(Float, nullable=False)  # 0–100
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    gaps: Mapped[list[str]] = mapped_column(JSON, default=list)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)

    candidate: Mapped["CandidateProfile"] = relationship()
    shortlist: Mapped["Shortlist"] = relationship(back_populates="entries")


class UsageEvent(Base):
    """
    Append-only per-agency usage metering (Phase 5.2). Each billable action
    records one row; monthly rollups drive the operator console and, later,
    usage-based billing.
    """
    __tablename__ = "rec_usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(
        ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class AgencyInvite(Base):
    """
    A pending recruiter-seat invite (Phase 5.5). An owner creates one; the
    recipient claims it via a one-time token to set their own password. Kept
    inside the data wall — no consumer references.
    """
    __tablename__ = "rec_agency_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(
        ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    role: Mapped[RecruiterSeatRole] = mapped_column(
        SAEnum(RecruiterSeatRole), default=RecruiterSeatRole.recruiter, nullable=False
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Application(Base, RecTimestampMixin):
    """Tracking-only pipeline record (Domain 2). Never a live submission."""
    __tablename__ = "rec_applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[int | None] = mapped_column(ForeignKey("rec_roles.id", ondelete="SET NULL"))

    company_name: Mapped[str | None] = mapped_column(String(200))
    job_title: Mapped[str | None] = mapped_column(String(200))
    stage: Mapped[ApplicationStage] = mapped_column(
        SAEnum(ApplicationStage), default=ApplicationStage.sourced, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Cached fit_score from the shortlist entry the candidate was assigned from,
    # so Kanban cards don't need to re-query the shortlist on every render.
    fit_score: Mapped[float | None] = mapped_column(Float)
    added_from_shortlist_id: Mapped[int | None] = mapped_column(Integer)

    # Per-candidate-in-this-role SWOT payload. Shape:
    #   {"strengths": [str], "weaknesses": [str], "opportunities": [str],
    #    "threats": [str], "generated_at": iso, "model": str}
    swot: Mapped[dict | None] = mapped_column(JSON)

    # ── Role-specific screening (Recruiter OS Phase 1) ────────────────────
    # This entity is effectively the CandidateRole join: candidate + role +
    # agency, with the recruiter's per-opportunity view of the candidate.
    # Screening data lives here (NOT on CandidateProfile) so the same person
    # can have different summaries, expected comp, notice, and motivation for
    # different roles.
    screening_outcome: Mapped[ScreeningOutcome | None] = mapped_column(
        SAEnum(ScreeningOutcome)
    )
    screening_completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    screening_completed_by: Mapped[int | None] = mapped_column(Integer)
    assigned_recruiter_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # Recruiter-authored narrative. `recruiter_summary` is CLIENT-VISIBLE by
    # default (subject to client_visibility). `internal_notes` is NEVER
    # exposed to clients regardless of the map.
    recruiter_summary: Mapped[str | None] = mapped_column(Text)
    candidate_motivation: Mapped[str | None] = mapped_column(Text)
    internal_notes: Mapped[str | None] = mapped_column(Text)
    motivation_categories: Mapped[list[str] | None] = mapped_column(JSON)

    # Structured compensation for this specific opportunity. Shape:
    #   {"amount": int, "currency": str, "period": "YEAR"|"MONTH"|"DAY"|"HOUR",
    #    "fixed"?: int, "variable"?: int, "bonus"?: int,
    #    "allowances"?: {...}, "rate"?: int, "rate_period"?: str,
    #    "contract_classification"?: str}
    expected_compensation: Mapped[dict | None] = mapped_column(JSON)
    current_compensation: Mapped[dict | None] = mapped_column(JSON)

    # Structured notice: {"value": int, "unit": "DAY"|"WEEK"|"MONTH",
    #                     "negotiable": bool, "available_from": iso date str?}
    notice_period: Mapped[dict | None] = mapped_column(JSON)

    availability_date: Mapped[date | None] = mapped_column(Date)
    availability_immediate: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )

    preferred_work_model: Mapped[WorkModel | None] = mapped_column(SAEnum(WorkModel))
    preferred_location: Mapped[str | None] = mapped_column(String(200))
    relocation: Mapped[RelocationPreference | None] = mapped_column(
        SAEnum(RelocationPreference)
    )
    relocation_notes: Mapped[str | None] = mapped_column(String(500))

    # Per-application map of field-name -> visible-to-client. Missing keys
    # resolve to DEFAULT_CLIENT_VISIBILITY. Never exposes internal_notes.
    client_visibility: Mapped[dict] = mapped_column(JSON, default=dict)

    candidate: Mapped["CandidateProfile"] = relationship()


class ApplicationNote(Base):
    """
    A single entry in an application's activity log. Two kinds:
      - "note"   : recruiter-authored free text (screen call, feedback, etc.)
      - "system" : auto-log of a stage change or SWOT regeneration.
    Kept separate from Application.notes (the legacy single-string field) so the
    log is append-only and preserves recruiter-by-recruiter attribution.
    """
    __tablename__ = "rec_application_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(
        ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True
    )
    application_id: Mapped[int] = mapped_column(
        ForeignKey("rec_applications.id", ondelete="CASCADE"), index=True
    )
    author_recruiter_id: Mapped[int | None] = mapped_column(Integer)
    author_name: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="note", server_default="note", nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RoleShareToken(Base):
    """
    A public read-only share link for a role — used to send a draft (or an
    active role) to a client with market context attached. Rotating the token
    revokes the previous URL. View count is bumped on every public GET so
    recruiters can see if the client actually opened it.
    """
    __tablename__ = "rec_role_share_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(
        ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("rec_roles.id", ondelete="CASCADE"), index=True
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    view_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RoleFeedback(Base):
    """
    Client-side sentiment (👍 / meh / 👎) plus optional comment on a role or
    on a specific shortlisted candidate. Submitted through the public share
    token — we store which token was used so a revoked link can't accept new
    feedback (checked in the endpoint).
    """
    __tablename__ = "rec_role_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(
        ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("rec_roles.id", ondelete="CASCADE"), index=True
    )
    share_token_id: Mapped[int | None] = mapped_column(
        ForeignKey("rec_role_share_tokens.id", ondelete="SET NULL")
    )
    candidate_id: Mapped[int | None] = mapped_column(Integer)
    sentiment: Mapped[int | None] = mapped_column(Integer)  # +1 / 0 / -1
    body: Mapped[str | None] = mapped_column(Text)
    client_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MarketSnapshot(Base):
    """
    Crawler-sourced compensation & demand data for a role query. Kept as
    lightweight aggregates (percentiles, top skills, competing titles) so the
    role-draft screen can show a "market context" panel that clients trust.
    """
    __tablename__ = "rec_market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(
        ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("rec_roles.id", ondelete="SET NULL"), index=True
    )
    query: Mapped[str] = mapped_column(String(300), nullable=False)
    location: Mapped[str | None] = mapped_column(String(200))
    sample_size: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    salary_p25: Mapped[int | None] = mapped_column(Integer)
    salary_p50: Mapped[int | None] = mapped_column(Integer)
    salary_p75: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="USD", server_default="USD")
    top_skills: Mapped[list[str] | None] = mapped_column(JSON)
    competing_roles: Mapped[list[str] | None] = mapped_column(JSON)
    sources: Mapped[list[str] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SpecSheetTemplate(Base, RecTimestampMixin):
    """
    Agency-owned CV / spec-sheet export template (Phase 1 feature 2).

    A template layers on top of the agency's default branding — any of the
    override fields left NULL falls back to the agency values (or to the
    renderer defaults if the agency hasn't set them either). This lets a
    single agency have, say, one "internal shortlist" template and one
    "client-safe anonymised" template without maintaining full duplicates.
    """
    __tablename__ = "rec_spec_sheet_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(
        ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Branding overrides — NULL means "inherit from agency defaults".
    logo_url: Mapped[str | None] = mapped_column(String(500))
    primary_color: Mapped[str | None] = mapped_column(String(9))
    header_text: Mapped[str | None] = mapped_column(String(300))
    footer_text: Mapped[str | None] = mapped_column(String(500))
    # Free-form intro paragraph rendered above the candidate's summary
    # (e.g. "Prepared for {client_name} — {agency} candidate submission").
    body_intro: Mapped[str | None] = mapped_column(Text)

    # If true, the template defaults `anonymise` to true on the export
    # endpoint when the caller doesn't pass a value. A convenience for
    # agencies that maintain a dedicated "anonymised" template.
    anonymise_by_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )




# ═════════════════════════════════════════════════════════════════════════
# Recruiter OS — Phase 2-7 domain objects
#
# Every table below is agency-scoped and rec_-prefixed. No FKs into consumer
# tables — the data wall stays intact. Cross-references between recruiter
# tables use ON DELETE CASCADE where the child has no meaning without its
# parent (e.g. Interview → Application), SET NULL where the parent's
# absence is survivable (e.g. Offer's assigned_recruiter_id).
# ═════════════════════════════════════════════════════════════════════════


# ── Phase 2: Consent + ClientSubmission ─────────────────────────────────
class CandidateConsent(Base, RecTimestampMixin):
    """
    Role-specific representation/submission consent (Phase 2). The same
    candidate consents per-role, per-agency — a candidate happy to be
    submitted to Role A hasn't consented to Role B.
    """
    __tablename__ = "rec_candidate_consents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[int] = mapped_column(ForeignKey("rec_roles.id", ondelete="CASCADE"), index=True)
    status: Mapped[ConsentStatus] = mapped_column(
        SAEnum(ConsentStatus), default=ConsentStatus.pending, nullable=False, index=True
    )
    method: Mapped[ConsentMethod | None] = mapped_column(SAEnum(ConsentMethod))
    captured_at: Mapped[datetime | None] = mapped_column(DateTime)
    captured_by: Mapped[int | None] = mapped_column(Integer)  # recruiter id
    evidence: Mapped[str | None] = mapped_column(Text)        # optional reference / URL / note
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


class ClientSubmission(Base, RecTimestampMixin):
    """
    A recruiter-authored candidate submission sent to a client.
    Snapshotted at submit time — later mutations to the source Application
    do NOT rewrite history a client already saw.
    """
    __tablename__ = "rec_client_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("rec_clients.id", ondelete="SET NULL"))
    role_id: Mapped[int] = mapped_column(ForeignKey("rec_roles.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"), index=True
    )
    application_id: Mapped[int] = mapped_column(
        ForeignKey("rec_applications.id", ondelete="CASCADE"), index=True
    )
    submitted_by: Mapped[int | None] = mapped_column(Integer)  # recruiter id
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[SubmissionStatus] = mapped_column(
        SAEnum(SubmissionStatus), default=SubmissionStatus.draft, nullable=False, index=True
    )

    # Snapshotted client-safe narrative + structured fields (frozen at submit).
    client_summary: Mapped[str | None] = mapped_column(Text)
    key_strengths: Mapped[list[str] | None] = mapped_column(JSON)
    potential_gaps: Mapped[list[str] | None] = mapped_column(JSON)
    compensation_snapshot: Mapped[dict | None] = mapped_column(JSON)
    notice_period_snapshot: Mapped[dict | None] = mapped_column(JSON)
    availability_snapshot: Mapped[dict | None] = mapped_column(JSON)
    motivation_snapshot: Mapped[str | None] = mapped_column(Text)
    cv_version_id: Mapped[str | None] = mapped_column(String(120))

    # Feedback / decision (mirrored from RoleFeedback for quick lookups).
    client_decision: Mapped[ClientDecision | None] = mapped_column(SAEnum(ClientDecision))
    client_feedback: Mapped[str | None] = mapped_column(Text)
    client_viewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    client_responded_at: Mapped[datetime | None] = mapped_column(DateTime)


# ── Phase 3: Structured client feedback + comparison + SLA ─────────────
class SubmissionFeedback(Base, RecTimestampMixin):
    """
    Structured client feedback on a submission. Extends the free-text
    RoleFeedback with a decision + reasons taxonomy so AI can spot patterns.
    """
    __tablename__ = "rec_submission_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("rec_client_submissions.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[ClientDecision] = mapped_column(SAEnum(ClientDecision), nullable=False)
    reasons: Mapped[list[str] | None] = mapped_column(JSON)   # list of FeedbackReason values
    comment: Mapped[str | None] = mapped_column(Text)
    client_contact_name: Mapped[str | None] = mapped_column(String(200))
    client_contact_email: Mapped[str | None] = mapped_column(String(255))
    submitted_via: Mapped[str | None] = mapped_column(String(40))  # "portal" | "email" | "manual"


class ClientSlaConfig(Base, RecTimestampMixin):
    """
    Per-client feedback SLA (Phase 3). If missing, we fall back to the
    agency-wide default in code (48h).
    """
    __tablename__ = "rec_client_sla_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("rec_clients.id", ondelete="CASCADE"), index=True, unique=True
    )
    expected_feedback_hours: Mapped[int] = mapped_column(Integer, default=48, server_default="48", nullable=False)
    notify_recruiter: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)


# ── Phase 4: Interviews ─────────────────────────────────────────────────
class Interview(Base, RecTimestampMixin):
    """
    An interview slot for a candidate on a role. Bound to the Application
    (candidate + role) rather than to the ClientSubmission so the record
    survives if a submission is re-issued.
    """
    __tablename__ = "rec_interviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("rec_applications.id", ondelete="CASCADE"), index=True
    )
    submission_id: Mapped[int | None] = mapped_column(
        ForeignKey("rec_client_submissions.id", ondelete="SET NULL")
    )
    role_id: Mapped[int] = mapped_column(ForeignKey("rec_roles.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[int | None] = mapped_column(ForeignKey("rec_clients.id", ondelete="SET NULL"))

    stage: Mapped[InterviewStage] = mapped_column(SAEnum(InterviewStage), default=InterviewStage.first)
    interview_type: Mapped[InterviewType | None] = mapped_column(SAEnum(InterviewType))
    interviewers: Mapped[list[dict] | None] = mapped_column(JSON)   # [{name, email, role}]
    proposed_times: Mapped[list[str] | None] = mapped_column(JSON)  # iso strings
    confirmed_time: Mapped[datetime | None] = mapped_column(DateTime)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    location: Mapped[str | None] = mapped_column(String(300))
    meeting_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[InterviewStatus] = mapped_column(
        SAEnum(InterviewStatus), default=InterviewStatus.scheduled, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)


class InterviewFeedback(Base, RecTimestampMixin):
    """Structured post-interview feedback."""
    __tablename__ = "rec_interview_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("rec_interviews.id", ondelete="CASCADE"), index=True
    )
    interviewer_name: Mapped[str | None] = mapped_column(String(200))
    technical: Mapped[int | None] = mapped_column(Integer)          # 1-5
    communication: Mapped[int | None] = mapped_column(Integer)
    role_understanding: Mapped[int | None] = mapped_column(Integer)
    domain_knowledge: Mapped[int | None] = mapped_column(Integer)
    leadership: Mapped[int | None] = mapped_column(Integer)
    culture_alignment: Mapped[int | None] = mapped_column(Integer)
    decision: Mapped[ClientDecision | None] = mapped_column(SAEnum(ClientDecision))
    comment: Mapped[str | None] = mapped_column(Text)


# ── Phase 5: Offers + Placements ────────────────────────────────────────
class Offer(Base, RecTimestampMixin):
    """
    A formal offer to a candidate on a role. Negotiation is recorded in
    OfferNegotiation rows; the top-level fields reflect the CURRENT state.
    """
    __tablename__ = "rec_offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("rec_applications.id", ondelete="CASCADE"), index=True
    )
    submission_id: Mapped[int | None] = mapped_column(
        ForeignKey("rec_client_submissions.id", ondelete="SET NULL")
    )
    role_id: Mapped[int] = mapped_column(ForeignKey("rec_roles.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[int | None] = mapped_column(ForeignKey("rec_clients.id", ondelete="SET NULL"))

    base_compensation: Mapped[dict | None] = mapped_column(JSON)   # CompensationBlock shape
    bonus: Mapped[dict | None] = mapped_column(JSON)
    equity: Mapped[dict | None] = mapped_column(JSON)
    allowances: Mapped[dict | None] = mapped_column(JSON)
    benefits: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    offer_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[OfferStatus] = mapped_column(
        SAEnum(OfferStatus), default=OfferStatus.draft, nullable=False, index=True
    )
    assigned_recruiter_id: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)


class OfferNegotiation(Base):
    """Append-only offer negotiation history."""
    __tablename__ = "rec_offer_negotiations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("rec_offers.id", ondelete="CASCADE"), index=True)
    round_label: Mapped[str] = mapped_column(String(60), default="counter")  # "initial" | "counter" | "final"
    from_party: Mapped[str | None] = mapped_column(String(20))   # "client" | "candidate" | "recruiter"
    compensation: Mapped[dict | None] = mapped_column(JSON)
    comment: Mapped[str | None] = mapped_column(Text)
    author_recruiter_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Placement(Base, RecTimestampMixin):
    """
    A closed hire — the final revenue-generating record. Created when an
    Offer is accepted; guarantee period drives post-placement reminders.
    """
    __tablename__ = "rec_placements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("rec_offers.id", ondelete="SET NULL"))
    role_id: Mapped[int] = mapped_column(ForeignKey("rec_roles.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("rec_candidate_profiles.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[int | None] = mapped_column(ForeignKey("rec_clients.id", ondelete="SET NULL"))
    recruiter_id: Mapped[int | None] = mapped_column(Integer)

    start_date: Mapped[date | None] = mapped_column(Date)
    final_compensation: Mapped[dict | None] = mapped_column(JSON)   # CompensationBlock
    fee_percent: Mapped[float | None] = mapped_column(Float)
    fee_amount: Mapped[int | None] = mapped_column(Integer)         # numeric, currency = final_compensation.currency
    guarantee_weeks: Mapped[int | None] = mapped_column(Integer)
    guarantee_ends_at: Mapped[date | None] = mapped_column(Date)
    status: Mapped[PlacementStatus] = mapped_column(
        SAEnum(PlacementStatus), default=PlacementStatus.upcoming, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)


class PostPlacementCheckin(Base):
    """Scheduled or completed check-ins during the guarantee period."""
    __tablename__ = "rec_post_placement_checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    placement_id: Mapped[int] = mapped_column(
        ForeignKey("rec_placements.id", ondelete="CASCADE"), index=True
    )
    day_offset: Mapped[int] = mapped_column(Integer)   # 7, 30, 60, 90
    due_date: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_by: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str | None] = mapped_column(String(40))  # "on_track" | "at_risk" | "replaced"
    notes: Mapped[str | None] = mapped_column(Text)


# ── Phase 6: Tasks + Notifications ──────────────────────────────────────
class RecruiterTask(Base, RecTimestampMixin):
    """
    A next-action item. Can be attached to any parent (application,
    submission, interview, offer, placement, role, client) or float free.
    AI can propose these; recruiter accepts them.
    """
    __tablename__ = "rec_recruiter_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    owner_recruiter_id: Mapped[int | None] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus), default=TaskStatus.open, nullable=False, index=True
    )
    priority: Mapped[TaskPriority] = mapped_column(
        SAEnum(TaskPriority), default=TaskPriority.medium, nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Loose parent references — no FKs so a task survives if its parent is
    # deleted (the task text still tells the recruiter what happened).
    parent_kind: Mapped[str | None] = mapped_column(String(30))
    parent_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # True when AI proposed it; recruiter accepts by editing / marking done.
    ai_generated: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )


class Notification(Base):
    """
    In-app notification. Grouped in the UI by (kind, parent). Delivery to
    email/push is a Phase 6.5 concern; this table is the persistent store.
    """
    __tablename__ = "rec_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    recruiter_id: Mapped[int | None] = mapped_column(Integer, index=True)
    kind: Mapped[NotificationKind] = mapped_column(SAEnum(NotificationKind), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    parent_kind: Mapped[str | None] = mapped_column(String(30))
    parent_id: Mapped[int | None] = mapped_column(Integer, index=True)
    priority: Mapped[TaskPriority] = mapped_column(
        SAEnum(TaskPriority), default=TaskPriority.medium, nullable=False
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


# ── Phase 7: AI intelligence cache ──────────────────────────────────────
class ClientShareToken(Base):
    """
    A public read-only client status link. One active token per client;
    rotating a token revokes the previous URL. The client-facing page shows
    role status, stage counts and an optional AI-generated summary — never
    candidate PII. view_count/last_viewed_at let the recruiter see if the
    client actually opened it.
    """
    __tablename__ = "rec_client_share_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(
        ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[int] = mapped_column(
        ForeignKey("rec_clients.id", ondelete="CASCADE"), index=True
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    view_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # AI-generated, client-safe status summary (bullets). Cached to keep the
    # public page fast; refreshed on demand from the recruiter-facing client page.
    ai_summary: Mapped[str | None] = mapped_column(Text)
    ai_summary_generated_at: Mapped[datetime | None] = mapped_column(DateTime)
    ai_summary_used_llm: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AiInsightCache(Base):
    """
    Cache for AI-derived insights (role pipeline health, client patterns,
    feedback rollups). Never authoritative — cheap-refresh, expiry-driven.
    """
    __tablename__ = "rec_ai_insight_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("rec_agencies.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(40), index=True)      # "role_health" | "client_intel" | "pipeline_health" | "feedback_pattern"
    scope_id: Mapped[int | None] = mapped_column(Integer, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    used_llm: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)


# All recruiter tables — used to create just these on startup without touching
# the consumer schema.
RECRUITER_TABLES = [
    Agency.__table__,
    Recruiter.__table__,
    Client.__table__,
    Role.__table__,
    CandidateProfile.__table__,
    CandidateSkill.__table__,
    CandidateExperience.__table__,
    Shortlist.__table__,
    ShortlistEntry.__table__,
    Application.__table__,
    ApplicationNote.__table__,
    RoleShareToken.__table__,
    RoleFeedback.__table__,
    UsageEvent.__table__,
    AgencyInvite.__table__,
    MarketSnapshot.__table__,
    SpecSheetTemplate.__table__,
    # Recruiter OS Phases 2-7
    CandidateConsent.__table__,
    ClientSubmission.__table__,
    SubmissionFeedback.__table__,
    ClientSlaConfig.__table__,
    Interview.__table__,
    InterviewFeedback.__table__,
    Offer.__table__,
    OfferNegotiation.__table__,
    Placement.__table__,
    PostPlacementCheckin.__table__,
    RecruiterTask.__table__,
    Notification.__table__,
    AiInsightCache.__table__,
    ClientShareToken.__table__,
]

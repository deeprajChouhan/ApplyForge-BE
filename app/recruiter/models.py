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
    EmploymentType,
    RecruiterSeatRole,
    RoleStatus,
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

    candidate: Mapped["CandidateProfile"] = relationship()


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
    UsageEvent.__table__,
    AgencyInvite.__table__,
    MarketSnapshot.__table__,
]

"""Pydantic request/response schemas for the recruiter API (Phase 1)."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.recruiter.enums import (
    AgencyPlan,
    AgencyStatus,
    ApplicationStage,
    BillingModel,
    EmploymentType,
    RecruiterSeatRole,
    RoleStatus,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Agency ────────────────────────────────────────────────────────────────
class AgencyCreate(BaseModel):
    name: str
    slug: str


class AgencyOut(ORMModel):
    id: int
    name: str
    slug: str
    plan: AgencyPlan = AgencyPlan.free
    created_at: datetime | None = None


class AgencyAdminOut(ORMModel):
    """Agency row for the operator console, with plan + seat usage + billing."""
    id: int
    name: str
    slug: str
    plan: AgencyPlan = AgencyPlan.free
    billing_model: BillingModel = BillingModel.flat
    subscription_status: str = "inactive"
    status: AgencyStatus = AgencyStatus.active
    trial_ends_at: datetime | None = None
    locked: bool = False
    seat_limit: int | None = None       # effective limit (None = unlimited)
    seats_used: int = 0
    features: list[str] = Field(default_factory=list)
    recruiter_count: int = 0
    created_at: datetime | None = None


class AgencyPlanUpdate(BaseModel):
    plan: AgencyPlan
    # Optional per-agency seat override (e.g. custom enterprise). Omit to use the
    # plan default.
    seat_limit: int | None = None
    # Optional per-agency billing model (flat vs per-seat). Omit to leave as-is.
    billing_model: BillingModel | None = None


class AgencyStatusUpdate(BaseModel):
    """Operator lifecycle control: approve/suspend/reactivate an agency."""
    status: AgencyStatus


class BillingSummaryOut(BaseModel):
    """Cross-agency oversight snapshot for the operator console (Phase 5.6)."""
    agencies_total: int
    by_status: dict[str, int]
    by_plan: dict[str, int]
    pending_approval: int
    locked: int
    active_subscriptions: int
    seats_used: int


# ── Self-serve onboarding (Phase 5.5) ─────────────────────────────────────
class AgencySignupRequest(BaseModel):
    agency_name: str
    owner_email: EmailStr
    owner_full_name: str | None = None
    password: str = Field(min_length=8)
    slug: str | None = None


class SignupResult(BaseModel):
    agency_id: int
    status: AgencyStatus
    pending_approval: bool
    message: str


class InviteCreate(BaseModel):
    email: EmailStr


class InviteOut(ORMModel):
    id: int
    email: str
    role: RecruiterSeatRole
    status: str
    expires_at: datetime | None = None
    created_at: datetime | None = None
    invite_url: str | None = None


class InvitePublicOut(BaseModel):
    """Safe, unauthenticated view of an invite for the claim page."""
    agency_name: str
    email: str
    valid: bool
    reason: str | None = None


class InviteAccept(BaseModel):
    password: str = Field(min_length=8)
    full_name: str | None = None


class BillingCheckoutRequest(BaseModel):
    plan: AgencyPlan


class BillingUrlOut(BaseModel):
    url: str


class UsageSummaryOut(BaseModel):
    agency_id: int
    month: str
    by_kind: dict[str, int]
    total: int


# ── Agency-admin tier (owner-scoped, Phase 5.3) ───────────────────────────
class AgencyOverviewOut(BaseModel):
    id: int
    name: str
    slug: str
    plan: AgencyPlan
    billing_model: BillingModel
    subscription_status: str
    billing_enabled: bool
    status: AgencyStatus = AgencyStatus.active
    trial_ends_at: datetime | None = None
    trial_days_left: int | None = None
    locked: bool = False
    seat_limit: int | None
    seats_used: int
    features: list[str]
    # Branding for the CV/spec-sheet export (Phase 1 feature 2).
    logo_url: str | None = None
    primary_color: str | None = None
    footer_text: str | None = None
    spec_sheet_template_id: int | None = None


class AgencyBrandingUpdate(BaseModel):
    """Owner-scoped update for the agency's export branding.

    Any field left unset keeps its current value; setting a field to an empty
    string clears it (so the agency can remove a logo, for example). The
    default spec-sheet template is set separately by omitting or passing an
    integer; pass 0 or null explicitly to clear.
    """
    logo_url: str | None = None
    primary_color: str | None = None
    footer_text: str | None = None
    spec_sheet_template_id: int | None = None


# ── Spec-sheet templates (Phase 1 feature 2) ──────────────────────────────
class SpecSheetTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    logo_url: str | None = None
    primary_color: str | None = None
    header_text: str | None = None
    footer_text: str | None = None
    body_intro: str | None = None
    anonymise_by_default: bool = False


class SpecSheetTemplateUpdate(BaseModel):
    """PATCH — any omitted field is left as-is."""
    name: str | None = Field(default=None, max_length=200)
    logo_url: str | None = None
    primary_color: str | None = None
    header_text: str | None = None
    footer_text: str | None = None
    body_intro: str | None = None
    anonymise_by_default: bool | None = None


class SpecSheetTemplateOut(ORMModel):
    id: int
    agency_id: int
    name: str
    logo_url: str | None = None
    primary_color: str | None = None
    header_text: str | None = None
    footer_text: str | None = None
    body_intro: str | None = None
    anonymise_by_default: bool = False


class TeamMemberCreate(BaseModel):
    email: EmailStr
    full_name: str | None = None
    password: str = Field(min_length=8)


class TeamMemberUpdate(BaseModel):
    full_name: str | None = None
    is_active: bool | None = None


# ── Clients ───────────────────────────────────────────────────────────────
class ClientCreate(BaseModel):
    name: str
    industry: str | None = None


class ClientOut(ORMModel):
    id: int
    agency_id: int
    name: str
    industry: str | None
    role_count: int = 0
    primary_contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    website: str | None = None
    address: str | None = None
    notes: str | None = None


class ClientUpdate(BaseModel):
    """Partial update for the client detail contact card."""
    name: str | None = None
    industry: str | None = None
    primary_contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    website: str | None = None
    address: str | None = None
    notes: str | None = None


class ClientRoleOut(BaseModel):
    """Compact role row for the client detail roles table."""
    id: int
    title: str
    status: str
    is_draft: bool = False
    seniority: str | None = None
    active_pipeline: int = 0
    placed: int = 0
    created_at: datetime | None = None


class ClientPlacementOut(BaseModel):
    application_id: int
    candidate_id: int
    candidate_name: str | None
    role_id: int | None
    role_title: str | None
    placed_at: datetime | None


class ClientAnalyticsOut(BaseModel):
    client_id: int
    roles_open: int
    roles_filled: int
    roles_draft: int
    roles_on_hold: int
    active_pipeline: int
    placements_total: int
    avg_time_to_fill_days: float | None
    top_skills: list[str]
    recent_placements: list[ClientPlacementOut]
    roles: list[ClientRoleOut]


# ── Next-hire advisory (company → next hire) ──────────────────────────────
class NextHireSuggestionOut(BaseModel):
    title: str
    rationale: str
    skills: list[str]
    pool_supply: int
    confidence: str  # low | medium | high


class NextHireAdvisoryOut(BaseModel):
    client_id: int
    client_name: str
    roster_roles: int
    suggestions: list[NextHireSuggestionOut]
    seniority_note: str | None


# ── Recruiter auth ────────────────────────────────────────────────────────
class RecruiterLoginRequest(BaseModel):
    email: EmailStr
    password: str


class RecruiterRefreshRequest(BaseModel):
    refresh_token: str


class RecruiterTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RecruiterMe(ORMModel):
    id: int
    email: str
    full_name: str | None
    role: RecruiterSeatRole
    agency: AgencyOut


# ── Recruiter management (admin-only) ─────────────────────────────────────
class RecruiterCreate(BaseModel):
    agency_id: int
    email: EmailStr
    full_name: str | None = None
    password: str = Field(min_length=8)
    role: RecruiterSeatRole = RecruiterSeatRole.recruiter


class RecruiterUpdate(BaseModel):
    full_name: str | None = None
    is_active: bool | None = None
    role: RecruiterSeatRole | None = None


class RecruiterPasswordReset(BaseModel):
    password: str = Field(min_length=8)


class RecruiterAdminOut(ORMModel):
    id: int
    agency_id: int
    agency_name: str | None = None
    email: str
    full_name: str | None
    role: RecruiterSeatRole
    is_active: bool
    created_at: datetime | None = None


# ── Role ──────────────────────────────────────────────────────────────────
class RoleCreate(BaseModel):
    title: str
    description: str | None = None
    client_id: int | None = None
    status: RoleStatus = RoleStatus.open
    employment_type: EmploymentType | None = None
    location: str | None = None
    seniority: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    min_years_experience: float | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    budget_min: int | None = None
    budget_max: int | None = None
    budget_currency: str = "USD"
    is_draft: bool = False
    notes: str | None = None


class ParseJDRequest(BaseModel):
    text: str = Field(min_length=10, max_length=20_000)


class ParseJDResult(BaseModel):
    title: str | None = None
    seniority: str | None = None
    employment_type: str | None = None
    location: str | None = None
    min_years_experience: float | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    budget_currency: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    description: str | None = None
    notes: str | None = None
    used_llm: bool = False


class ScreeningQuestion(BaseModel):
    text: str
    intent: str | None = None  # short label: "gap probe", "seniority check", etc.


class ScreeningQuestionsOut(BaseModel):
    questions: list[ScreeningQuestion]
    generated_at: datetime
    used_llm: bool


class AskCandidateRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    role_id: int | None = None


class EvidenceRef(BaseModel):
    id: str
    label: str


class AskCandidateResult(BaseModel):
    answer: str
    citations: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    generated_at: datetime
    used_llm: bool


class RoleUpdate(BaseModel):
    """Partial-update payload for the role detail page."""
    title: str | None = None
    description: str | None = None
    client_id: int | None = None
    status: RoleStatus | None = None
    employment_type: EmploymentType | None = None
    location: str | None = None
    seniority: str | None = None
    required_skills: list[str] | None = None
    preferred_skills: list[str] | None = None
    min_years_experience: float | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    budget_min: int | None = None
    budget_max: int | None = None
    budget_currency: str | None = None
    is_draft: bool | None = None
    notes: str | None = None


class RoleOut(ORMModel):
    id: int
    agency_id: int
    client_id: int | None
    title: str
    description: str | None
    status: RoleStatus
    employment_type: EmploymentType | None
    location: str | None
    seniority: str | None
    required_skills: list[str]
    preferred_skills: list[str]
    min_years_experience: float | None
    salary_min: int | None
    salary_max: int | None
    budget_min: int | None = None
    budget_max: int | None = None
    budget_currency: str = "USD"
    is_draft: bool = False
    market_snapshot: dict | None = None
    notes: str | None = None


# ── Candidate ─────────────────────────────────────────────────────────────
class CandidateSkillOut(ORMModel):
    name: str


class CandidateOut(ORMModel):
    id: int
    agency_id: int
    full_name: str | None
    email: str | None
    phone: str | None
    headline: str | None
    location: str | None
    years_experience: float | None
    summary: str | None
    provisioned_user_id: int | None = None
    expected_budget_min: int | None = None
    expected_budget_max: int | None = None
    expected_budget_currency: str = "USD"
    skills: list[CandidateSkillOut] = Field(default_factory=list)


class CandidateBudgetUpdate(BaseModel):
    expected_budget_min: int | None = None
    expected_budget_max: int | None = None
    expected_budget_currency: str = "USD"


class CandidateExperienceOut(ORMModel):
    id: int
    title: str | None
    company: str | None
    start_date: date | None = None
    end_date: date | None = None
    description: str | None


class CandidateDetailOut(CandidateOut):
    """Full-profile payload for the shared candidate drawer."""
    phone: str | None = None
    location: str | None = None
    summary: str | None = None
    source_file: str | None = None
    experiences: list[CandidateExperienceOut] = Field(default_factory=list)


# ── Provisioning bridge (convert profile → consumer user) ─────────────────
class ConvertRequest(BaseModel):
    consent: bool = False
    email: str | None = None  # optional override when the profile has no email


class ConvertResult(BaseModel):
    candidate_id: int
    provisioned_user_id: int
    email: str


class IngestResultItem(BaseModel):
    candidate_id: int
    full_name: str | None
    email: str | None
    skill_count: int


class IngestResult(BaseModel):
    ingested: int
    candidates: list[IngestResultItem]


# ── LinkedIn capture (Chrome extension → pool) ────────────────────────────
class LinkedInCaptureExperience(BaseModel):
    title: str | None = None
    company: str | None = None
    # Loose date strings — LinkedIn typically exposes "YYYY-MM"; the service
    # coerces "YYYY", "YYYY-MM", or "YYYY-MM-DD".
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None


class LinkedInCaptureRequest(BaseModel):
    """Payload posted by the recruiter Chrome extension after scraping a
    public linkedin.com/in/<slug> page. All fields except linkedin_url are
    best-effort — real profiles vary widely and the scraper degrades gracefully."""
    linkedin_url: str
    full_name: str | None = None
    headline: str | None = None
    location: str | None = None
    about: str | None = None
    email: str | None = None
    phone: str | None = None
    skills: list[str] = Field(default_factory=list)
    experiences: list[LinkedInCaptureExperience] = Field(default_factory=list)
    # Optional: attach the captured candidate to this role's pipeline at
    # stage=sourced. Idempotent — the same (role, candidate) pair is not
    # duplicated on re-capture.
    role_id: int | None = None


class LinkedInCaptureResult(BaseModel):
    candidate_id: int
    full_name: str | None
    email: str | None
    linkedin_url: str
    skill_count: int
    created: bool                 # True on first capture; False on dedup/refresh
    application_id: int | None = None


# ── Placement (candidate → roles) ─────────────────────────────────────────
class RoleMatchOut(BaseModel):
    role_id: int
    title: str
    seniority: str | None
    status: str
    fit_score: float
    reasons: list[str]
    gaps: list[str]
    score_breakdown: dict


class CandidateRoleMatchesOut(BaseModel):
    candidate_id: int
    matches: list[RoleMatchOut]


# ── Shortlist / matching ──────────────────────────────────────────────────
class ShortlistEntryOut(ORMModel):
    candidate_id: int
    rank: int
    fit_score: float
    reasons: list[str]
    gaps: list[str]
    score_breakdown: dict


class ShortlistOut(ORMModel):
    id: int
    role_id: int
    created_at: datetime | None = None
    entries: list[ShortlistEntryOut] = Field(default_factory=list)


# ── Job listing generation ────────────────────────────────────────────────
class JobListingOut(BaseModel):
    role_id: int
    title: str
    seniority: str | None
    location: str | None
    employment_type: str | None
    salary_range: str | None
    summary: str
    responsibilities: list[str]
    requirements: list[str]
    nice_to_have: list[str]
    top_pool_skills: list[str]
    candidate_sample: int
    content_markdown: str
    polished_by_llm: bool


# ── Market analytics ──────────────────────────────────────────────────────
class SkillDemandSupplyOut(BaseModel):
    skill: str
    demand: int
    supply: int
    shortage: bool


class SalarySummaryOut(BaseModel):
    count: int
    avg_min: int | None
    avg_max: int | None
    overall_min: int | None
    overall_max: int | None


class StageCountOut(BaseModel):
    stage: str
    count: int


class MarketOverviewOut(BaseModel):
    roles_total: int
    roles_open: int
    candidates_total: int
    placements: int
    time_to_fill_days: float | None
    skills: list[SkillDemandSupplyOut]
    shortages: list[SkillDemandSupplyOut]
    salary: SalarySummaryOut
    pipeline_funnel: list[StageCountOut]


class MarketCrawlResult(BaseModel):
    snapshots: list[MarketSnapshotOut]
    total: int


# ── Application (tracking) ─────────────────────────────────────────────────
class ApplicationCreate(BaseModel):
    candidate_id: int
    role_id: int | None = None
    company_name: str | None = None
    job_title: str | None = None
    stage: ApplicationStage = ApplicationStage.sourced
    notes: str | None = None


class ApplicationStageUpdate(BaseModel):
    stage: ApplicationStage


class ApplicationOut(ORMModel):
    id: int
    candidate_id: int
    role_id: int | None
    company_name: str | None
    job_title: str | None
    stage: ApplicationStage
    notes: str | None
    fit_score: float | None = None
    added_from_shortlist_id: int | None = None
    swot: dict | None = None
    last_activity_at: datetime | None = None


class SwotOut(BaseModel):
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    threats: list[str] = Field(default_factory=list)
    generated_at: datetime
    model: str


class AssignCandidatesRequest(BaseModel):
    """
    Attach one or more shortlisted candidates to a role's pipeline. Idempotent:
    a candidate already in the pipeline is skipped (not duplicated).
    """
    candidate_ids: list[int] = Field(default_factory=list, min_length=1)
    stage: ApplicationStage = ApplicationStage.sourced
    shortlist_id: int | None = None


class AssignCandidatesResult(BaseModel):
    added: list[ApplicationOut]
    skipped_existing: list[int]


class RoleBoardColumn(BaseModel):
    stage: ApplicationStage
    applications: list[ApplicationOut]


class RoleBoardOut(BaseModel):
    """Kanban payload — one column per pipeline stage, ordered by fit."""
    role_id: int
    columns: list[RoleBoardColumn]
    total: int


class ApplicationNoteOut(ORMModel):
    id: int
    application_id: int
    author_recruiter_id: int | None
    author_name: str | None
    kind: str
    body: str
    created_at: datetime | None = None


class ApplicationNoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


class RoleShareTokenOut(ORMModel):
    id: int
    role_id: int
    token: str
    is_active: bool
    view_count: int
    last_viewed_at: datetime | None = None
    created_at: datetime | None = None
    # Convenience — the URL is assembled server-side using the request base if
    # available; frontend fallbacks to `${window.location.origin}/public/roles/{token}`.
    share_url: str | None = None


class PublicShortlistCandidate(BaseModel):
    """Client-facing shortlist row — first-name-only, no email/phone."""
    candidate_id: int
    display_name: str
    headline: str | None
    years_experience: float | None
    fit_score: float
    top_skills: list[str]


class PublicRoleView(BaseModel):
    """Client-safe payload — no candidate PII, no client budget internals."""
    role_id: int
    title: str
    seniority: str | None
    location: str | None
    employment_type: str | None
    description: str | None
    required_skills: list[str]
    preferred_skills: list[str]
    min_years_experience: float | None
    salary_min: int | None
    salary_max: int | None
    market_snapshot: dict | None
    is_draft: bool
    agency_name: str
    shortlist: list[PublicShortlistCandidate] = Field(default_factory=list)


class PublicFeedbackCreate(BaseModel):
    candidate_id: int | None = None
    sentiment: int | None = None  # +1 / 0 / -1
    body: str | None = None
    client_name: str | None = None


class RoleFeedbackOut(ORMModel):
    id: int
    role_id: int
    candidate_id: int | None
    sentiment: int | None
    body: str | None
    client_name: str | None
    created_at: datetime | None = None


class MarketSnapshotOut(ORMModel):
    id: int
    role_id: int | None
    query: str
    location: str | None
    sample_size: int
    salary_p25: int | None
    salary_p50: int | None
    salary_p75: int | None
    currency: str
    top_skills: list[str] | None
    competing_roles: list[str] | None
    sources: list[str] | None
    created_at: datetime | None = None

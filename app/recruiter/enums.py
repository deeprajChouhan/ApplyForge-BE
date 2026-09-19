import enum


class RecruiterSeatRole(str, enum.Enum):
    """A seat's role within an agency."""
    owner = "owner"       # agency lead / agency admin
    recruiter = "recruiter"


class AgencyPlan(str, enum.Enum):
    """Billing tier for an agency tenant (Phase 5)."""
    free = "free"
    pro = "pro"
    enterprise = "enterprise"


class BillingModel(str, enum.Enum):
    """How an agency is billed on a paid plan (chosen per agency, Phase 5.4)."""
    flat = "flat"          # fixed monthly price per plan (Stripe quantity = 1)
    per_seat = "per_seat"  # price × recruiter seats (Stripe quantity = seat count)


class AgencyStatus(str, enum.Enum):
    """Lifecycle of an agency tenant (Phase 5.5, operator-approved signup)."""
    pending = "pending"      # self-signed-up, awaiting operator approval — can't log in
    active = "active"        # approved / operator-created — normal access
    suspended = "suspended"  # operator-suspended — login blocked


class InviteStatus(str, enum.Enum):
    """State of an agency seat invite (Phase 5.5 invite/claim flow)."""
    pending = "pending"
    accepted = "accepted"
    revoked = "revoked"


# Free trial length for self-serve signups; at expiry the agency locks until it
# has an active paid subscription (Phase 5.5).
TRIAL_DAYS = 14
# Seat invites expire if unclaimed after this many days.
INVITE_TTL_DAYS = 14


# Max recruiter seats per plan. None = unlimited.
PLAN_SEAT_LIMITS: dict[AgencyPlan, int | None] = {
    AgencyPlan.free: 2,
    AgencyPlan.pro: 10,
    AgencyPlan.enterprise: None,
}

# Gated "AI insight" features unlocked on paid plans. Core matching + tracking
# (pool, roles, shortlist, pipeline, placement, clients) is available on all plans.
AGENCY_FEATURES = ("listings", "market", "advisory")

PLAN_FEATURES: dict[AgencyPlan, set[str]] = {
    AgencyPlan.free: set(),
    AgencyPlan.pro: set(AGENCY_FEATURES),
    AgencyPlan.enterprise: set(AGENCY_FEATURES),
}


def default_seat_limit(plan: AgencyPlan) -> int | None:
    return PLAN_SEAT_LIMITS.get(plan, PLAN_SEAT_LIMITS[AgencyPlan.free])


# ── Usage metering (Phase 5.2) ────────────────────────────────────────────
# Billable actions recorded per agency. Cheap reads (e.g. viewing the market
# dashboard) are intentionally not metered.
class UsageKind(str, enum.Enum):
    cv_ingested = "cv_ingested"
    shortlist_generated = "shortlist_generated"
    listing_drafted = "listing_drafted"
    role_match_run = "role_match_run"
    advisory_run = "advisory_run"
    spec_sheet_exported = "spec_sheet_exported"   # branded CV/spec-sheet PDF or DOCX


def plan_has_feature(plan: AgencyPlan, feature: str) -> bool:
    return feature in PLAN_FEATURES.get(plan, set())


class RoleStatus(str, enum.Enum):
    open = "open"
    on_hold = "on_hold"
    filled = "filled"
    closed = "closed"


class EmploymentType(str, enum.Enum):
    full_time = "full_time"
    part_time = "part_time"
    contract = "contract"
    internship = "internship"
    temporary = "temporary"


class CandidateSource(str, enum.Enum):
    """How a CandidateProfile entered the pool."""
    bulk_cv = "bulk_cv"
    linkedin = "linkedin"   # captured from linkedin.com/in/* via the recruiter Chrome extension
    manual = "manual"
    ats_sync = "ats_sync"
    referral = "referral"


class ApplicationStage(str, enum.Enum):
    """Tracking-only pipeline stages (Domain 2). Never a live submission."""
    sourced = "sourced"
    submitted = "submitted"
    screening = "screening"
    interview = "interview"
    offer = "offer"
    placed = "placed"
    rejected = "rejected"


ACTIVE_STAGES = {
    ApplicationStage.sourced,
    ApplicationStage.submitted,
    ApplicationStage.screening,
    ApplicationStage.interview,
    ApplicationStage.offer,
}


# ── Recruiter OS: role-specific screening (Phase 1) ──────────────────────

class ScreeningOutcome(str, enum.Enum):
    """Recruiter's suitability verdict for a candidate on a specific role."""
    suitable = "suitable"
    maybe = "maybe"
    not_suitable = "not_suitable"


class WorkModel(str, enum.Enum):
    remote = "remote"
    hybrid = "hybrid"
    onsite = "onsite"
    flexible = "flexible"


class RelocationPreference(str, enum.Enum):
    yes = "yes"
    no = "no"
    conditional = "conditional"


class NoticeUnit(str, enum.Enum):
    day = "DAY"
    week = "WEEK"
    month = "MONTH"


class CompensationPeriod(str, enum.Enum):
    hour = "HOUR"
    day = "DAY"
    week = "WEEK"
    month = "MONTH"
    year = "YEAR"


class MotivationCategory(str, enum.Enum):
    career_progression = "career_progression"
    compensation = "compensation"
    technology = "technology"
    leadership = "leadership"
    relocation = "relocation"
    remote_flexibility = "remote_flexibility"
    company_stability = "company_stability"
    industry_interest = "industry_interest"
    product_ownership = "product_ownership"
    team_environment = "team_environment"
    other = "other"


# Client-visibility default map. Any key missing from Application.client_visibility
# resolves to the value here. `internal_notes` and `screening_outcome` are NEVER
# client-visible — the visibility serializer ignores the map for those two.
CLIENT_VISIBLE_FIELDS = (
    "recruiter_summary",
    "candidate_motivation",
    "expected_compensation",
    "notice_period",
    "availability",
    "preferred_work_model",
    "preferred_location",
    "relocation",
)

# Fields that must NEVER leave the recruiter tenant regardless of visibility map.
NEVER_CLIENT_VISIBLE = (
    "internal_notes",
    "screening_outcome",
    "current_compensation",
    "assigned_recruiter_id",
    "screening_completed_by",
    "motivation_categories",  # detail; the recruiter_summary carries the client-safe narrative
)

DEFAULT_CLIENT_VISIBILITY = {f: True for f in CLIENT_VISIBLE_FIELDS}


# ── Recruiter OS: Phase 2-7 enums ──────────────────────────────────────

class ConsentStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    declined = "declined"
    expired = "expired"


class ConsentMethod(str, enum.Enum):
    email = "email"
    phone = "phone"
    portal = "portal"
    manual = "manual"


class SubmissionStatus(str, enum.Enum):
    draft = "draft"
    submitted = "submitted"
    client_reviewing = "client_reviewing"
    progressed = "progressed"
    on_hold = "on_hold"
    rejected = "rejected"
    withdrawn = "withdrawn"


class ClientDecision(str, enum.Enum):
    progress = "progress"
    hold = "hold"
    reject = "reject"


class FeedbackReason(str, enum.Enum):
    technical_experience = "technical_experience"
    industry_experience = "industry_experience"
    seniority = "seniority"
    salary = "salary"
    location = "location"
    availability = "availability"
    notice_period = "notice_period"
    communication = "communication"
    role_changed = "role_changed"
    other = "other"


class InterviewStage(str, enum.Enum):
    screen = "screen"
    first = "first"
    second = "second"
    final = "final"
    other = "other"


class InterviewType(str, enum.Enum):
    phone = "phone"
    video = "video"
    onsite = "onsite"
    technical = "technical"
    panel = "panel"


class InterviewStatus(str, enum.Enum):
    scheduled = "scheduled"
    completed = "completed"
    cancelled = "cancelled"
    rescheduled = "rescheduled"
    no_show = "no_show"


class OfferStatus(str, enum.Enum):
    draft = "draft"
    sent = "sent"
    negotiating = "negotiating"
    accepted = "accepted"
    declined = "declined"
    withdrawn = "withdrawn"


class PlacementStatus(str, enum.Enum):
    upcoming = "upcoming"
    started = "started"
    completed = "completed"
    cancelled = "cancelled"
    refunded = "refunded"
    replaced = "replaced"


class TaskStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    done = "done"
    dismissed = "dismissed"


class TaskPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class NotificationKind(str, enum.Enum):
    candidate_screening_due = "candidate_screening_due"
    consent_missing = "consent_missing"
    submission_viewed = "submission_viewed"
    client_feedback_received = "client_feedback_received"
    feedback_overdue = "feedback_overdue"
    interview_requested = "interview_requested"
    interview_upcoming = "interview_upcoming"
    interview_feedback_missing = "interview_feedback_missing"
    offer_updated = "offer_updated"
    offer_expiring = "offer_expiring"
    candidate_accepted = "candidate_accepted"
    candidate_declined = "candidate_declined"
    start_date_upcoming = "start_date_upcoming"
    guarantee_ending = "guarantee_ending"
    stale_pipeline = "stale_pipeline"
    other = "other"

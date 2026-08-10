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
    """How a CandidateProfile entered the pool. Phase 1 ships bulk_cv only."""
    bulk_cv = "bulk_cv"
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

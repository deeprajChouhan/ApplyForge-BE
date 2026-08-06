import enum


class RecruiterSeatRole(str, enum.Enum):
    """A seat's role within an agency."""
    owner = "owner"       # agency lead / agency admin
    recruiter = "recruiter"


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

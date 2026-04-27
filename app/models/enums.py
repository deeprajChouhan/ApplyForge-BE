import enum


class ApplicationStatus(str, enum.Enum):
    draft = "draft"
    ready = "ready"
    applied = "applied"
    follow_up = "follow_up"
    interview = "interview"
    replied = "replied"
    rejected = "rejected"
    offer = "offer"
    archived = "archived"


class DocumentType(str, enum.Enum):
    resume = "resume"
    cover_letter = "cover_letter"
    cold_email = "cold_email"
    cold_message = "cold_message"


class FileType(str, enum.Enum):
    resume = "resume"
    other = "other"


class UserRole(str, enum.Enum):
    admin = "admin"
    user = "user"


class PlanTier(str, enum.Enum):
    free = "free"
    pro = "pro"
    enterprise = "enterprise"


class SubscriptionStatus(str, enum.Enum):
    active = "active"
    trialing = "trialing"
    cancelled = "cancelled"
    past_due = "past_due"


class FeatureFlag(str, enum.Enum):
    """
    Granular feature flags for per-user SaaS access control.

    free tier gets: jd_analyze, applications, resume (full apply workflow)
    pro/enterprise get: all features (+ kanban, chat)
    admin can grant/revoke any feature on any user individually.
    """
    jd_analyze = "jd_analyze"          # Analyze JD + generate documents
    applications = "applications"       # Save & manage job applications list
    kanban = "kanban"                   # Kanban board view (pro+)
    resume = "resume"                   # Resume upload, parsing, and knowledge base
    chat = "chat"                       # AI chat assistant per application (pro+)

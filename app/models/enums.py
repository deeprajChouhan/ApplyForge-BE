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
    multi_resume = "multi_resume"       # Multiple resumes + per-application RAG (pro+)
    job_crawler = "job_crawler"         # Automated daily job discovery crawler (pro+)
    job_discovery = "job_discovery"     # Curated job discovery feed (pro+)
    interview_practice = "interview_practice"  # AI mock interview practice (pro+)
    package_generation = "package_generation"  # One-click application package (all plans, free=limited)


class SupportTicketStatus(str, enum.Enum):
    open = "open"
    pending = "pending"
    resolved = "resolved"
    closed = "closed"


class SupportTicketPriority(str, enum.Enum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class InterviewSessionStatus(str, enum.Enum):
    in_progress = "in_progress"
    completed = "completed"


class WorkType(str, enum.Enum):
    remote = "remote"
    hybrid = "hybrid"
    onsite = "onsite"
    any = "any"


class CrawlSource(str, enum.Enum):
    remoteok = "remoteok"
    arbeitnow = "arbeitnow"
    jobicy = "jobicy"
    linkedin = "linkedin"
    adzuna = "adzuna"

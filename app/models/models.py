from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ApplicationStatus, DocumentType, FeatureFlag, FileType, PlanTier, SubscriptionStatus, UserRole

# Default monthly token budgets per plan tier
PLAN_TOKEN_BUDGETS: dict[str, int] = {
    PlanTier.free: 50_000,
    PlanTier.pro: 500_000,
    PlanTier.enterprise: 5_000_000,
}

# Features automatically granted per plan on registration
PLAN_DEFAULT_FEATURES: dict[str, list] = {
    PlanTier.free: [FeatureFlag.jd_analyze, FeatureFlag.applications, FeatureFlag.resume, FeatureFlag.package_generation],
    PlanTier.pro: [FeatureFlag.jd_analyze, FeatureFlag.applications, FeatureFlag.kanban, FeatureFlag.resume, FeatureFlag.chat, FeatureFlag.multi_resume, FeatureFlag.job_crawler, FeatureFlag.job_discovery, FeatureFlag.interview_practice, FeatureFlag.package_generation],
    PlanTier.enterprise: [FeatureFlag.jd_analyze, FeatureFlag.applications, FeatureFlag.kanban, FeatureFlag.resume, FeatureFlag.chat, FeatureFlag.multi_resume, FeatureFlag.job_crawler, FeatureFlag.job_discovery, FeatureFlag.interview_practice, FeatureFlag.package_generation],
}

# Monthly application-package generation limits per plan (-1 = unlimited)
PLAN_PACKAGE_LIMITS: dict[str, int] = {
    PlanTier.free: 5,
    PlanTier.pro: -1,
    PlanTier.enterprise: -1,
}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.user, index=True)
    plan: Mapped[PlanTier] = mapped_column(Enum(PlanTier), default=PlanTier.free, index=True)
    subscription_status: Mapped[SubscriptionStatus] = mapped_column(Enum(SubscriptionStatus), default=SubscriptionStatus.active)
    token_budget_monthly: Mapped[int] = mapped_column(Integer, default=PLAN_TOKEN_BUDGETS[PlanTier.free])


class RefreshToken(Base, TimestampMixin):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    headline: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(255))
    phone_number: Mapped[str | None] = mapped_column(String(50))
    age: Mapped[int | None] = mapped_column(Integer)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    current_role: Mapped[str | None] = mapped_column(String(255))
    career_goals: Mapped[str | None] = mapped_column(Text)
    target_roles: Mapped[str | None] = mapped_column(Text)  # JSON list[str]
    preferred_locations: Mapped[str | None] = mapped_column(Text)  # JSON list[str]
    salary_expectation: Mapped[str | None] = mapped_column(String(100))
    deal_breakers: Mapped[str | None] = mapped_column(Text)  # JSON list[str]


class WorkExperience(Base, TimestampMixin):
    __tablename__ = "work_experiences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    company: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[datetime | None] = mapped_column(Date)
    end_date: Mapped[datetime | None] = mapped_column(Date)


class Education(Base, TimestampMixin):
    __tablename__ = "educations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    institution: Mapped[str] = mapped_column(String(255))
    degree: Mapped[str | None] = mapped_column(String(255))
    field_of_study: Mapped[str | None] = mapped_column(String(255))
    start_date: Mapped[datetime | None] = mapped_column(Date)
    end_date: Mapped[datetime | None] = mapped_column(Date)


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    technologies: Mapped[str | None] = mapped_column(String(255))


class Skill(Base, TimestampMixin):
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_skill_user_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    level: Mapped[str | None] = mapped_column(String(50))


class Certification(Base, TimestampMixin):
    __tablename__ = "certifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    issuer: Mapped[str | None] = mapped_column(String(255))
    issue_date: Mapped[datetime | None] = mapped_column(Date)


class UploadedFile(Base, TimestampMixin):
    __tablename__ = "uploaded_files"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    file_type: Mapped[FileType] = mapped_column(Enum(FileType))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(100))
    path: Mapped[str] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(Integer)


class ParsedResumeData(Base, TimestampMixin):
    __tablename__ = "parsed_resume_data"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    uploaded_file_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_files.id", ondelete="SET NULL"))
    raw_text: Mapped[str] = mapped_column(Text)
    structured_json: Mapped[str] = mapped_column(Text)
    confidence_score: Mapped[float] = mapped_column(Float)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "knowledge_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(50))
    source_ref: Mapped[str | None] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    # When set, this document belongs to a specific uploaded resume's knowledge base.
    # NULL = legacy full-profile index (backward-compatible).
    parsed_resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("parsed_resume_data.id", ondelete="CASCADE"), nullable=True, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "knowledge_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    # Nullable: vectors are stored in Qdrant; this column is kept for schema
    # compatibility but is no longer populated by RAGService.
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Mirrors parsed_resume_id from the parent KnowledgeDocument for fast filtering.
    parsed_resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("parsed_resume_data.id", ondelete="CASCADE"), nullable=True, index=True
    )
    __table_args__ = (Index("idx_chunks_doc_chunk", "document_id", "chunk_index"),)


class JobApplication(Base, TimestampMixin):
    __tablename__ = "job_applications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    company_name: Mapped[str] = mapped_column(String(255), index=True)
    role_title: Mapped[str] = mapped_column(String(255), index=True)
    job_description: Mapped[str] = mapped_column(Text)
    jd_link: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[ApplicationStatus] = mapped_column(Enum(ApplicationStatus), index=True, default=ApplicationStatus.draft)
    jd_analysis_json: Mapped[str | None] = mapped_column(Text)
    # Priority Score sub-scores (populated after analyze_jd or /score)
    fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    competition_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    priority_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # PRO: which parsed resume this application uses for RAG and document generation.
    # NULL = use the latest parsed resume (free-tier / default behaviour).
    selected_resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("parsed_resume_data.id", ondelete="SET NULL"), nullable=True, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    delete_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class GeneratedDocument(Base, TimestampMixin):
    __tablename__ = "generated_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("job_applications.id", ondelete="CASCADE"), index=True)
    doc_type: Mapped[DocumentType] = mapped_column(Enum(DocumentType), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(String(20), default="txt")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class ApplicationChat(Base, TimestampMixin):
    __tablename__ = "application_chats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("job_applications.id", ondelete="CASCADE"), unique=True)


class ApplicationChatMessage(Base, TimestampMixin):
    __tablename__ = "application_chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("application_chats.id", ondelete="CASCADE"), index=True)
    sender_role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)


class ApplicationStatusHistory(Base, TimestampMixin):
    __tablename__ = "application_status_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("job_applications.id", ondelete="CASCADE"), index=True)
    old_status: Mapped[ApplicationStatus | None] = mapped_column(Enum(ApplicationStatus))
    new_status: Mapped[ApplicationStatus] = mapped_column(Enum(ApplicationStatus), index=True)
    note: Mapped[str | None] = mapped_column(String(500))


# ── SaaS Feature / Usage Tables ────────────────────────────────────────────

class UserFeature(Base, TimestampMixin):
    """Per-user feature flag override. Admin can grant or revoke individual features."""
    __tablename__ = "user_features"
    __table_args__ = (UniqueConstraint("user_id", "feature", name="uq_user_feature"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    feature: Mapped[FeatureFlag] = mapped_column(Enum(FeatureFlag), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class UsageLedger(Base):
    """Aggregated monthly token usage per user. Updated on every AI call."""
    __tablename__ = "usage_ledger"
    __table_args__ = (UniqueConstraint("user_id", "month_year", name="uq_ledger_user_month"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    month_year: Mapped[str] = mapped_column(String(7), index=True)  # e.g. "2026-04"
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    api_calls: Mapped[int] = mapped_column(Integer, default=0)
    packages_used: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UsageEvent(Base):
    """Granular per-API-call token usage log. Used for admin analytics and billing."""
    __tablename__ = "usage_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    feature: Mapped[FeatureFlag | None] = mapped_column(Enum(FeatureFlag), nullable=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(100))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ApplicationCustomization(Base, TimestampMixin):
    """
    Per-application resume customizations applied from AI suggestions.
    Stored separately from the master profile so each job gets its own
    tailored resume without polluting shared profile data.

    customizations_json structure:
    {
        "skills_add":          [{"name": "Docker", "level": "intermediate"}, ...],
        "experiences_update":  {"<exp_id>": {"description": "improved bullets…"}, ...},
        "projects_add":        [{"name": "…", "description": "…", "technologies": "…"}, …]
    }
    """
    __tablename__ = "application_customizations"
    id:             Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id:        Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id", ondelete="CASCADE"), unique=True, index=True
    )
    customizations_json: Mapped[str] = mapped_column(Text, default="{}")


# LinkedIn Connections (Phase 2 -- LinkedIn CSV ingestion)

class LinkedInConnection(Base, TimestampMixin):
    """
    One LinkedIn connection imported from the user's Connections CSV export.
    Unique per (user_id, full_name) -- re-importing the same person updates
    their company/position rather than creating a duplicate row.
    """
    __tablename__ = "linkedin_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "full_name", name="uq_linkedin_conn_user_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    position: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connected_on: Mapped[date | None] = mapped_column(Date, nullable=True)


# ── Job Crawler Feature ────────────────────────────────────────────────────

class CrawlerConfig(Base, TimestampMixin):
    """
    Per-user configuration for the automated job discovery crawler.
    One row per user (created when they first configure the crawler).
    """
    __tablename__ = "crawler_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # What to search for
    job_roles: Mapped[str | None] = mapped_column(Text, nullable=True, default="[]")   # JSON array of role keywords
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[str] = mapped_column(String(10), default="USD")
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g. "us", "gb", "in"
    work_type: Mapped[str] = mapped_column(String(20), default="any")         # remote/hybrid/onsite/any

    # Which resume to match against (NULL = latest)
    selected_resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("parsed_resume_data.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Daily application goal
    daily_goal: Mapped[int] = mapped_column(Integer, default=10)

    # When the crawler last ran successfully
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CrawledJob(Base, TimestampMixin):
    """
    A job discovered by the daily crawler for a specific user.
    Unique per (user_id, source, external_id) — prevents re-adding the same posting.
    """
    __tablename__ = "crawled_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "external_id", name="uq_crawled_job_user_source_ext"),
        Index("ix_crawled_jobs_user_crawled", "user_id", "crawled_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    source: Mapped[str] = mapped_column(String(50))          # "remoteok" | "arbeitnow" | "adzuna"
    external_id: Mapped[str] = mapped_column(String(255))    # Job posting ID on the source site

    title: Mapped[str] = mapped_column(String(255))
    company: Mapped[str] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    work_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    salary_range: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    apply_url: Mapped[str] = mapped_column(String(1000))
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON array of tags/skills

    # AI scoring: how well does this job match the user's resume/preferences?
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)    # 0-100
    match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)       # Short AI explanation

    crawled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # User actions
    is_dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_saved: Mapped[bool] = mapped_column(Boolean, default=False)         # added to applications
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_applications.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


# ── Platform Foundation: audit, analytics, plans, support, interview ──────


class AuditLog(Base):
    """Append-only record of state-changing actions across the platform."""
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_actor_created", "actor_user_id", "created_at"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    before: Mapped[str | None] = mapped_column(Text, nullable=True)
    after: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ProductEvent(Base):
    """First-party product/website analytics event."""
    __tablename__ = "product_events"
    __table_args__ = (
        Index("ix_product_events_name_created", "event_name", "created_at"),
        Index("ix_product_events_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    event_name: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    properties: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    referrer: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Plan(Base, TimestampMixin):
    """Admin-managed pricing plan, surfaced via public /plans and admin CRUD."""
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_monthly: Mapped[float] = mapped_column(Float, default=0)
    price_yearly: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="usd")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    features: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of feature labels
    limits: Mapped[str | None] = mapped_column(Text, nullable=True)    # JSON object of limit key/values
    cta_label: Mapped[str | None] = mapped_column(String(50), nullable=True)
    highlighted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class SupportTicket(Base, TimestampMixin):
    """User-submitted help desk ticket."""
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(50), default="general")
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    related_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    related_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned_admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class UserExtensionState(Base, TimestampMixin):
    """Per-user state for the ApplyForge Job Clipper browser extension."""
    __tablename__ = "user_extension_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    promo_dismissed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SupportTicketMessage(Base):
    """A single message within a support ticket thread."""
    __tablename__ = "support_ticket_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True)
    sender_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    sender_role: Mapped[str] = mapped_column(String(20), default="user")
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InterviewSession(Base, TimestampMixin):
    """A mock interview practice session for a user, optionally tied to an application."""
    __tablename__ = "interview_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("job_applications.id", ondelete="SET NULL"), nullable=True, index=True)
    role_title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="in_progress", index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    delete_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class InterviewAnswer(Base, TimestampMixin):
    """A single question/answer/feedback unit within an interview session."""
    __tablename__ = "interview_answers"
    __table_args__ = (Index("ix_interview_answers_session_q", "session_id", "question_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"), index=True)
    question_index: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

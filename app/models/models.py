from datetime import datetime
from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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
    PlanTier.free: [FeatureFlag.jd_analyze, FeatureFlag.applications, FeatureFlag.resume],
    PlanTier.pro: [FeatureFlag.jd_analyze, FeatureFlag.applications, FeatureFlag.kanban, FeatureFlag.resume, FeatureFlag.chat],
    PlanTier.enterprise: [FeatureFlag.jd_analyze, FeatureFlag.applications, FeatureFlag.kanban, FeatureFlag.resume, FeatureFlag.chat],
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


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "knowledge_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(50))
    source_ref: Mapped[str | None] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "knowledge_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[str] = mapped_column(Text)
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


class GeneratedDocument(Base, TimestampMixin):
    __tablename__ = "generated_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("job_applications.id", ondelete="CASCADE"), index=True)
    doc_type: Mapped[DocumentType] = mapped_column(Enum(DocumentType), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(String(20), default="txt")


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


from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.job_source import JobSource


class RemoteMode(str, enum.Enum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class SubmitMethod(str, enum.Enum):
    ATS_API = "ats_api"
    PLAYWRIGHT = "playwright"
    EXTENSION = "extension"
    MANUAL = "manual"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("ats_provider", "external_id", name="uq_jobs_ats_provider_external_id"),
        Index("ix_jobs_is_active_last_seen_at", "is_active", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    ats_provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # NOTE: the underlying MySQL column is ENUM('onsite','hybrid','remote','unknown')
    # from migration 0001_auto_apply_core. We read/write via plain String(32)
    # to bypass SQLAlchemy's enum name/value mapping brittleness. MySQL
    # continues to validate the column values against the ENUM at write time.
    remote_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown", server_default="unknown"
    )

    employment_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seniority: Mapped[str | None] = mapped_column(String(64), nullable=True)

    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    description_html: Mapped[str | None] = mapped_column(Text, nullable=True)

    apply_url: Mapped[str] = mapped_column(String(2048), nullable=False)

    submit_method: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual", server_default="manual"
    )

    jd_analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    company: Mapped["Company"] = relationship("Company", back_populates="jobs")
    sources: Mapped[list["JobSource"]] = relationship(
        "JobSource", back_populates="job", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"Job(id={self.id!r}, title={self.title!r}, ats_provider={self.ats_provider!r})"

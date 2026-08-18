from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AutoApplySettings(Base, TimestampMixin):
    __tablename__ = "auto_apply_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    target_titles_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    locations_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    remote_only: Mapped[bool] = mapped_column(Boolean, default=False)
    min_match_score: Mapped[int] = mapped_column(Integer, default=70)
    daily_cap: Mapped[int] = mapped_column(Integer, default=20)
    weekly_cap: Mapped[int] = mapped_column(Integer, default=100)
    excluded_companies_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    excluded_keywords_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    default_kit_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fully_automatic: Mapped[bool] = mapped_column(Boolean, default=False)
    paused_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    payload_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )


class AutoApplyRun(Base):
    __tablename__ = "auto_apply_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    jobs_considered: Mapped[int] = mapped_column(Integer, default=0)
    jobs_queued: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

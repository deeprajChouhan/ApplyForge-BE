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
    UniqueConstraint,
)  # noqa: F401
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ApplicationKit(Base, TimestampMixin):
    __tablename__ = "application_kits"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_application_kits_user_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)

    # Soft reference to a resume record — this codebase stores resumes in
    # `uploaded_files` / `parsed_resume_data` rather than a single `resumes`
    # table, so we keep this as a plain nullable Integer and enforce validity
    # in the service layer instead of via a DB-level FK.
    base_resume_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    tone: Mapped[str] = mapped_column(String(32), default="professional", nullable=False)
    cover_letter_style: Mapped[str] = mapped_column(
        String(32), default="standard", nullable=False
    )

    default_answers_ref_json: Mapped[Optional[list]] = mapped_column(
        JSON, nullable=True
    )

    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

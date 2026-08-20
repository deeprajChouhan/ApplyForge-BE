from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class FieldType(str, PyEnum):
    SHORT_TEXT = "short_text"
    LONG_TEXT = "long_text"
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    BOOLEAN = "boolean"
    NUMBER = "number"
    DATE = "date"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    FILE = "file"
    EEOC = "eeoc"


class AnswerLibrary(Base, TimestampMixin):
    __tablename__ = "answer_library"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "question_key", name="uq_answer_library_user_question"
        ),
        Index("ix_answer_library_user_field", "user_id", "field_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    question_key: Mapped[str] = mapped_column(String(64), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_embedding_json: Mapped[Optional[list]] = mapped_column(
        JSON, nullable=True
    )

    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    field_type: Mapped[FieldType] = mapped_column(
        Enum(FieldType, name="field_type_enum", values_callable=lambda x: [e.value for e in x]),
        default=FieldType.SHORT_TEXT,
        nullable=False,
    )

    tags: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="user", nullable=False)

    times_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )

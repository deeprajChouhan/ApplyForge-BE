"""
app/models/evaluation_models.py
--------------------------------
SQLAlchemy models for the Evaluation Infrastructure.

Tables:
  llm_usage_logs        – per-call cost / latency tracking
  application_evaluations – scorer + hallucination results per application
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApplicationOutcome(str):
    """
    Not a DB enum — used for Pydantic validation only.
    Stored as a plain String column for forward-compatibility.
    """
    INTERVIEW   = "interview"
    REJECTED    = "rejected"
    NO_RESPONSE = "no_response"
    OFFER       = "offer"


class LLMUsageLog(Base):
    """
    Per-LLM-call usage log.

    Populated by the track_llm_call decorator in evaluation/cost_tracker.py.
    """
    __tablename__ = "llm_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Optional link back to an application (can be NULL for non-application LLM calls)
    application_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("job_applications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # LLM metadata
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    operation:  Mapped[str] = mapped_column(String(100), nullable=False, default="generate")

    # Token counts
    prompt_tokens:     Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens:      Mapped[int] = mapped_column(Integer, default=0)

    # Estimated cost in USD
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    # Performance
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)

    # Timestamps
    called_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )

    __table_args__ = (
        Index("idx_llm_logs_user_date", "user_id", "called_at"),
        Index("idx_llm_logs_app",       "application_id"),
    )


class ApplicationEvaluation(Base):
    """
    Evaluation results (scores + hallucination flags) for a generated document.

    One row per (application_id, doc_type) combination — upserted on each
    (re)generation so we always have the most recent scores.
    """
    __tablename__ = "application_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False, default="cover_letter")

    # ── Scorer fields ──────────────────────────────────────────────────────────
    ats_keyword_match:   Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tone_score:          Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    length_score:        Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    experience_relevance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    overall_score:       Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_method:        Mapped[Optional[str]]   = mapped_column(String(20), nullable=True)
    score_reasoning_json: Mapped[Optional[str]]  = mapped_column(Text, nullable=True)

    # ── Hallucination fields ───────────────────────────────────────────────────
    hallucination_flags_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hallucination_count:      Mapped[int]            = mapped_column(Integer, default=0)

    # ── Outcome tracking ───────────────────────────────────────────────────────
    # interview | rejected | no_response | offer  (NULL = not recorded yet)
    outcome: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)

    # Timestamps
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("idx_eval_app_doc", "application_id", "doc_type"),
    )

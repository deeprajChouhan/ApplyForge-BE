"""
app/schemas/evaluation.py
--------------------------
Pydantic schemas for the evaluation endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, field_validator


class OutcomeRequest(BaseModel):
    """Body for POST /applications/{id}/outcome"""
    outcome: Literal["interview", "rejected", "no_response", "offer"]


class ScoreDetail(BaseModel):
    """Evaluation score breakdown for a single document."""
    doc_type:             str
    ats_keyword_match:    Optional[float] = None
    tone_score:           Optional[float] = None
    length_score:         Optional[float] = None
    experience_relevance: Optional[float] = None
    overall_score:        Optional[float] = None
    score_method:         Optional[str]   = None
    reasoning:            Optional[dict[str, str]] = None
    hallucination_count:  int = 0
    hallucination_flags:  list[dict[str, Any]] = []
    outcome:              Optional[str] = None
    evaluated_at:         Optional[datetime] = None

    model_config = {"from_attributes": True}


class ApplicationScoreResponse(BaseModel):
    """Response for GET /applications/{id}/score"""
    application_id: int
    evaluations:    list[ScoreDetail]


class OutcomeResponse(BaseModel):
    """Response for POST /applications/{id}/outcome"""
    application_id: int
    outcome:        str
    message:        str

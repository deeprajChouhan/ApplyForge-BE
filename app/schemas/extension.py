"""Schemas for the ApplyForge Job Clipper browser extension endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ExtensionStatusOut(BaseModel):
    connected: bool
    connected_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    promo_dismissed_at: Optional[datetime] = None
    chrome_store_url: Optional[str] = None


class ScorePreviewIn(BaseModel):
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""
    source_url: str = ""
    source_site: str = ""


class ScorePreviewMeta(BaseModel):
    seniority: Optional[str] = None
    work_type: Optional[str] = None
    contract_type: Optional[str] = None


class ExtensionScorePreviewOut(BaseModel):
    priority_score: float
    label: str
    fit_score: float
    opportunity_score: float
    competition_score: float
    score_confidence: str  # "low" | "medium" | "high"
    recommendation: str
    summary: str = ""
    job_summary: str = ""
    why_score: str = ""
    key_requirements: list[str] = []
    reply_likelihood: float = 0.0
    reply_probability: float = 0.0
    reply_label: str = ""
    reply_reasoning: str = ""
    required_yoe: Optional[int] = None
    detected_seniority: Optional[str] = None
    work_type: str = ""
    contract_type: str = ""
    meta: ScorePreviewMeta = ScorePreviewMeta()


class SaveJobIn(BaseModel):
    title: str
    company: str = ""
    location: str = ""
    description: str = ""
    source_url: str = ""
    source_site: str = ""
    priority_score: Optional[float] = None
    score_confidence: Optional[str] = None


class SaveJobOut(BaseModel):
    success: bool
    application_id: str
    open_url: str


class ExtensionEventIn(BaseModel):
    event: str
    properties: Optional[dict] = None

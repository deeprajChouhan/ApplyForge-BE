"""Schemas for the ApplyForge Job Clipper browser extension endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


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
    # ── Job Hunting settings–driven fields ──
    fit_source: str = "neutral"  # "neutral" | "resume_quick"
    fit_breakdown: dict[str, float] = {}
    resume_id: Optional[int] = None
    resume_label: Optional[str] = None
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    must_have_found: list[str] = []
    must_have_missing: list[str] = []
    excluded_company: bool = False
    already_saved_application_id: Optional[str] = None


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


# ── Job Hunting → Extension settings ────────────────────────────────────────

SUPPORTED_SITE_KEYS = ("linkedin", "indeed", "glassdoor", "other")
WidgetPosition = Literal["inline", "top-right", "top-left", "bottom-right", "bottom-left"]
SaveStatus = Literal["draft", "ready", "applied"]


def _clean_terms(values: list[str], max_items: int = 30, max_len: int = 60) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in values or []:
        t = " ".join(str(v).split())[:max_len]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
        if len(out) >= max_items:
            break
    return out


class ExtensionSettings(BaseModel):
    """Per-user preferences that drive the Job Clipper. Every field has a default."""

    enabled: bool = True
    enabled_sites: list[str] = Field(default_factory=lambda: list(SUPPORTED_SITE_KEYS))
    # Parsed resume to score and save against. None = latest parsed resume.
    default_resume_id: Optional[int] = None
    # Score automatically when a job is opened (False = one-click "Score this job").
    auto_score: bool = True
    # Use resume skills + experience for a quick fit instead of the neutral 50.
    quick_fit: bool = True
    show_card_labels: bool = True
    widget_position: WidgetPosition = "inline"
    must_have_keywords: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    default_save_status: SaveStatus = "draft"
    open_after_save: bool = False
    warn_duplicates: bool = True

    @field_validator("enabled_sites")
    @classmethod
    def _sites(cls, v: list[str]) -> list[str]:
        return [s for s in dict.fromkeys(x.lower() for x in v) if s in SUPPORTED_SITE_KEYS]

    @field_validator("must_have_keywords", "excluded_companies")
    @classmethod
    def _terms(cls, v: list[str]) -> list[str]:
        return _clean_terms(v)


class ExtensionSettingsUpdate(BaseModel):
    """Partial update — only provided fields change."""

    enabled: Optional[bool] = None
    enabled_sites: Optional[list[str]] = None
    default_resume_id: Optional[int] = None
    clear_default_resume: bool = False
    auto_score: Optional[bool] = None
    quick_fit: Optional[bool] = None
    show_card_labels: Optional[bool] = None
    widget_position: Optional[WidgetPosition] = None
    must_have_keywords: Optional[list[str]] = None
    excluded_companies: Optional[list[str]] = None
    default_save_status: Optional[SaveStatus] = None
    open_after_save: Optional[bool] = None
    warn_duplicates: Optional[bool] = None


class ExtensionSettingsOut(ExtensionSettings):
    default_resume_label: Optional[str] = None

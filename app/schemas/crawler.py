"""
Pydantic schemas for the Job Crawler feature.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator


# ── Config ─────────────────────────────────────────────────────────────────

class CrawlerConfigUpdate(BaseModel):
    is_enabled: Optional[bool] = None
    job_roles: Optional[List[str]] = None
    salary_min: Optional[int] = Field(None, ge=0)
    salary_max: Optional[int] = Field(None, ge=0)
    salary_currency: Optional[str] = Field(None, max_length=10)
    country: Optional[str] = Field(None, max_length=100)
    work_type: Optional[str] = Field(None, pattern="^(remote|hybrid|onsite|any)$")
    selected_resume_id: Optional[int] = None
    daily_goal: Optional[int] = Field(None, ge=1, le=100)


class CrawlerConfigOut(BaseModel):
    id: int
    user_id: int
    is_enabled: bool
    job_roles: List[str]
    salary_min: Optional[int]
    salary_max: Optional[int]
    salary_currency: str
    country: Optional[str]
    work_type: str
    selected_resume_id: Optional[int]
    daily_goal: int
    last_run_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("job_roles", mode="before")
    @classmethod
    def parse_job_roles(cls, v):
        import json
        if isinstance(v, str):
            return json.loads(v)
        return v


# ── Crawled Job ─────────────────────────────────────────────────────────────

class CrawledJobOut(BaseModel):
    id: int
    user_id: int
    source: str
    external_id: str
    title: str
    company: str
    location: Optional[str]
    work_type: Optional[str]
    salary_range: Optional[str]
    description: Optional[str]
    apply_url: str
    tags: Optional[List[str]]
    match_score: Optional[float]
    match_reason: Optional[str]
    crawled_at: datetime
    is_dismissed: bool
    is_saved: bool
    application_id: Optional[int]

    model_config = {"from_attributes": True}

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags(cls, v):
        import json
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return []
        return v or []


class CrawledJobAction(BaseModel):
    """Used to dismiss or save a crawled job."""
    is_dismissed: Optional[bool] = None
    is_saved: Optional[bool] = None


class CrawlTriggerResponse(BaseModel):
    message: str
    jobs_found: int
    jobs_added: int
    skipped: bool = False
    skip_reason: Optional[str] = None  # "no_roles" | "not_enabled"

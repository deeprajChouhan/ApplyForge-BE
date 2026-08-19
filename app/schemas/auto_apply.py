from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict

AutoApplyStage = Literal[
    "queued",
    "preparing",
    "awaiting_review",
    "submitting",
    "submitted",
    "failed",
    "declined",
    "needs_answer",
]


class AutoApplySettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    is_active: bool
    target_titles_json: Optional[list[str]] = None
    locations_json: Optional[list[str]] = None
    remote_only: bool
    min_match_score: int
    daily_cap: int
    weekly_cap: int
    excluded_companies_json: Optional[list[str]] = None
    excluded_keywords_json: Optional[list[str]] = None
    default_kit_id: Optional[int] = None
    fully_automatic: bool
    paused_at: Optional[datetime] = None
    willing_to_relocate: Optional[bool] = None
    min_salary: Optional[int] = None
    salary_currency: Optional[str] = None
    default_resume_parse_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class AutoApplySettingsUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    is_active: Optional[bool] = None
    target_titles_json: Optional[list[str]] = None
    locations_json: Optional[list[str]] = None
    remote_only: Optional[bool] = None
    min_match_score: Optional[int] = None
    daily_cap: Optional[int] = None
    weekly_cap: Optional[int] = None
    excluded_companies_json: Optional[list[str]] = None
    excluded_keywords_json: Optional[list[str]] = None
    default_kit_id: Optional[int] = None
    fully_automatic: Optional[bool] = None
    paused_at: Optional[datetime] = None
    willing_to_relocate: Optional[bool] = None
    min_salary: Optional[int] = None
    salary_currency: Optional[str] = None
    default_resume_parse_id: Optional[int] = None


class AutoApplyQueueItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    application_id: int
    company_name: Optional[str] = None
    role_title: Optional[str] = None
    match_score: Optional[int] = None
    match_reasons: Optional[list[str]] = None
    auto_apply_stage: Optional[AutoApplyStage] = None
    updated_at: Optional[datetime] = None
    apply_url: Optional[str] = None
    job_id: Optional[int] = None


class AutoApplyQueueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[AutoApplyQueueItem]
    next_cursor: Optional[str] = None
    counts: dict[str, int]


class ApplicationEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    payload: Optional[dict[str, Any]] = None
    created_at: datetime


class AutoApplyRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    started_at: datetime
    finished_at: Optional[datetime] = None
    jobs_considered: int
    jobs_queued: int
    error_text: Optional[str] = None


class ApproveApplicationIn(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DeclineApplicationIn(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reason: Optional[str] = None

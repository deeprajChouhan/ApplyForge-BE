"""Pydantic v2 schemas used as the normalized return type from ATS providers.

ATS provider adapters (Greenhouse, Lever, Workday, etc.) should parse their
native payloads into these normalized shapes so downstream code (job
ingestion, dedup, auto-apply) can operate on a single consistent contract
regardless of source.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class RemoteMode(str, Enum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class SubmitMethod(str, Enum):
    ATS_API = "ats_api"
    PLAYWRIGHT = "playwright"
    EXTENSION = "extension"
    MANUAL = "manual"


class NormalizedCompany(BaseModel):
    """Normalized company payload produced by an ATS provider adapter."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    ats_provider: str
    ats_slug: str
    careers_url: str | None = None
    domain: str | None = None
    size_bucket: str | None = None
    industry: str | None = None


class NormalizedJob(BaseModel):
    """Normalized job payload produced by an ATS provider adapter."""

    model_config = ConfigDict(from_attributes=True)

    company: NormalizedCompany

    ats_provider: str
    external_id: str

    title: str
    location: str | None = None

    remote_mode: RemoteMode = RemoteMode.UNKNOWN
    employment_type: str | None = None
    seniority: str | None = None

    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = Field(default=None, max_length=3)

    description: str
    description_html: str | None = None

    apply_url: str
    submit_method: SubmitMethod = SubmitMethod.MANUAL

    jd_analysis_json: dict | None = None

    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    is_active: bool = True

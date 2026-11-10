"""
Partner API request/response schemas.

Response models are explicit allow-lists. Internal fields — internal_notes,
recruiter summaries, budgets, fees, compensation, embeddings, raw CV text,
storage locations, market snapshots, provisioned_user_id — are never part of
any partner response model, so they can't leak through a careless return.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.recruiter.enums import ApplicationStage, EmploymentType, RoleStatus

_REF_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


def _clean_ref(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if not _REF_RE.match(v):
        raise ValueError("external_ref may contain letters, digits and . _ : - (max 128)")
    return v


def _clean_skills(v: list[str] | None) -> list[str]:
    out: list[str] = []
    for s in v or []:
        s = (s or "").strip()
        if s and len(s) <= 60 and s.lower() not in {x.lower() for x in out}:
            out.append(s)
    return out[:50]


# ── Common ──────────────────────────────────────────────────────────────
class Page(BaseModel):
    has_more: bool
    next_offset: int | None = None


class PartnerError(BaseModel):
    error: str
    message: str


# ── Me ─────────────────────────────────────────────────────────────────
class MeOut(BaseModel):
    agency_id: int
    agency_name: str
    key_name: str
    key_prefix: str
    kind: str
    environment: str
    client_id: int | None = None
    scopes: list[str]
    rate_limit_per_minute: int


# ── Jobs ───────────────────────────────────────────────────────────────
class JobWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    status: RoleStatus | None = None
    is_draft: bool | None = None
    employment_type: EmploymentType | None = None
    location: str | None = Field(default=None, max_length=200)
    country_code: str | None = None
    seniority: str | None = Field(default=None, max_length=80)
    required_skills: list[str] | None = None
    preferred_skills: list[str] | None = None
    min_years_experience: float | None = Field(default=None, ge=0, le=60)
    salary_min: int | None = Field(default=None, ge=0, le=100_000_000)
    salary_max: int | None = Field(default=None, ge=0, le=100_000_000)
    currency: str | None = None
    client_id: int | None = None
    client_external_ref: str | None = None
    external_ref: str | None = None

    _ref = field_validator("external_ref", "client_external_ref")(lambda cls, v: _clean_ref(v))
    _skills = field_validator("required_skills", "preferred_skills")(
        lambda cls, v: None if v is None else _clean_skills(v)
    )

    @field_validator("country_code")
    @classmethod
    def _country(cls, v):
        if v is None:
            return None
        v = v.strip().upper()
        if not _COUNTRY_RE.match(v):
            raise ValueError("country_code must be ISO 3166-1 alpha-2")
        return v

    @field_validator("currency")
    @classmethod
    def _currency(cls, v):
        if v is None:
            return None
        v = v.strip().upper()
        if not _CURRENCY_RE.match(v):
            raise ValueError("currency must be a 3-letter ISO code")
        return v


class JobCreate(JobWrite):
    title: str = Field(min_length=1, max_length=200)


class JobOut(BaseModel):
    id: int
    external_ref: str | None = None
    title: str
    description: str | None = None
    status: RoleStatus
    is_draft: bool
    employment_type: EmploymentType | None = None
    location: str | None = None
    country_code: str | None = None
    seniority: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    min_years_experience: float | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str | None = None
    client_id: int | None = None
    source: str
    accepting_applications: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
    pipeline: dict[str, int] | None = None


class JobList(Page):
    data: list[JobOut]


class PublicJobOut(BaseModel):
    """Careers-page view: what you'd publish on a job board. No client, no budget."""
    id: int
    title: str
    description: str | None = None
    employment_type: EmploymentType | None = None
    location: str | None = None
    country_code: str | None = None
    seniority: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str | None = None
    posted_at: datetime | None = None


class PublicJobList(Page):
    data: list[PublicJobOut]


class JobParseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=20, max_length=20000)


class JobParseOut(BaseModel):
    used_llm: bool
    draft: dict


# ── Applications ───────────────────────────────────────────────────────
class ApplicationReceipt(BaseModel):
    """What a publishable (browser) key gets back — reveals nothing about existing records."""
    status: Literal["received"] = "received"


class ApplicationCreated(BaseModel):
    application_id: int
    candidate_id: int
    job_id: int
    stage: ApplicationStage
    processing_status: str
    duplicate: bool = False
    test_mode: bool = False


class DocumentOut(BaseModel):
    id: int
    kind: str
    filename: str
    content_type: str | None = None
    size_bytes: int
    created_at: datetime | None = None


class MatchExplanation(BaseModel):
    reasons: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    breakdown: dict[str, float] = Field(default_factory=dict)
    parse_quality: dict | None = None


class ApplicationOut(BaseModel):
    id: int
    job_id: int | None = None
    candidate_id: int
    external_ref: str | None = None
    stage: ApplicationStage
    source: str
    processing_status: str
    fit_score: float | None = None
    match: MatchExplanation | None = None
    candidate_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_activity_at: datetime | None = None


class ApplicationList(Page):
    data: list[ApplicationOut]


class StageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: ApplicationStage
    reason: str | None = Field(default=None, max_length=300)


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=5000)


class NoteOut(BaseModel):
    id: int
    body: str
    created_at: datetime | None = None


class InterviewOut(BaseModel):
    id: int
    stage: str
    interview_type: str | None = None
    status: str
    confirmed_time: datetime | None = None
    timezone: str | None = None
    duration_minutes: int | None = None


class OfferOut(BaseModel):
    id: int
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PlacementOut(BaseModel):
    id: int
    status: str
    start_date: date | None = None


class ApplicationStatusOut(BaseModel):
    application_id: int
    stage: ApplicationStage
    interviews: list[InterviewOut] = Field(default_factory=list)
    offers: list[OfferOut] = Field(default_factory=list)
    placement: PlacementOut | None = None


# ── Candidates ─────────────────────────────────────────────────────────
class CandidateOut(BaseModel):
    id: int
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    headline: str | None = None
    location: str | None = None
    years_experience: float | None = None
    skills: list[str] = Field(default_factory=list)
    linkedin_url: str | None = None
    source: str
    created_at: datetime | None = None
    documents: list[DocumentOut] = Field(default_factory=list)


class CandidateCreated(BaseModel):
    candidate_id: int
    created: bool
    test_mode: bool = False


class ErasureOut(BaseModel):
    candidate_id: int
    erased: bool
    files_removed: int
    applications_removed: int


# ── Clients ────────────────────────────────────────────────────────────
class ClientWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    industry: str | None = Field(default=None, max_length=120)
    website: str | None = Field(default=None, max_length=300)
    external_ref: str | None = None

    _ref = field_validator("external_ref")(lambda cls, v: _clean_ref(v))


class ClientOut(BaseModel):
    id: int
    name: str
    industry: str | None = None
    website: str | None = None
    external_ref: str | None = None
    created_at: datetime | None = None


class ClientList(Page):
    data: list[ClientOut]


# ── Matching ───────────────────────────────────────────────────────────
class ShortlistEntryOut(BaseModel):
    rank: int
    candidate_id: int
    candidate_name: str | None = None
    fit_score: float
    reasons: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    breakdown: dict[str, float] = Field(default_factory=dict)


class ShortlistOut(BaseModel):
    job_id: int
    shortlist_id: int
    created_at: datetime | None = None
    entries: list[ShortlistEntryOut]


class UsageOut(BaseModel):
    month: str
    by_kind: dict[str, int]
    total: int
    requests_this_month: int


class StageInfo(BaseModel):
    stage: str
    order: int
    terminal: bool


class EventInfo(BaseModel):
    event: str
    description: str


class MetaOut(BaseModel):
    stages: list[StageInfo]
    events: list[EventInfo]
    scopes: dict[str, str]


# ── Application form (validated from multipart fields) ─────────────────
class ApplicantForm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    linkedin_url: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=200)
    cover_letter: str | None = Field(default=None, max_length=10000)
    answers: list[dict] | None = None
    consent: bool
    privacy_notice_version: str = Field(min_length=1, max_length=40)
    consent_text: str | None = Field(default=None, max_length=2000)
    source_site: str | None = Field(default=None, max_length=200)
    utm_source: str | None = Field(default=None, max_length=100)
    utm_medium: str | None = Field(default=None, max_length=100)
    utm_campaign: str | None = Field(default=None, max_length=100)
    external_ref: str | None = None

    _ref = field_validator("external_ref")(lambda cls, v: _clean_ref(v))

    @field_validator("first_name", "last_name", "location", "source_site")
    @classmethod
    def _no_markup(cls, v):
        if v is None:
            return v
        v = v.strip()
        if re.search(r"[<>\x00-\x1f]", v):
            raise ValueError("contains invalid characters")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not re.fullmatch(r"[0-9+()\-. ]{5,40}", v):
            raise ValueError("invalid phone number")
        return v

    @field_validator("linkedin_url")
    @classmethod
    def _linkedin(cls, v):
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not re.fullmatch(r"https://([a-z]{2,3}\.)?linkedin\.com/[A-Za-z0-9/_\-%.]+/?", v):
            raise ValueError("must be an https://linkedin.com/… URL")
        return v

    @field_validator("consent")
    @classmethod
    def _consent(cls, v):
        if v is not True:
            raise ValueError("the applicant must consent to the privacy notice")
        return v

    @field_validator("answers")
    @classmethod
    def _answers(cls, v):
        if v is None:
            return None
        if len(v) > 30:
            raise ValueError("at most 30 answers")
        out = []
        for item in v:
            if not isinstance(item, dict):
                raise ValueError("answers must be objects with question/answer")
            q = str(item.get("question", "")).strip()[:300]
            a = str(item.get("answer", "")).strip()[:5000]
            if q:
                out.append({"question": q, "answer": a})
        return out

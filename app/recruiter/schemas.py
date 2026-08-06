"""Pydantic request/response schemas for the recruiter API (Phase 1)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.recruiter.enums import ApplicationStage, EmploymentType, RecruiterSeatRole, RoleStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Agency ────────────────────────────────────────────────────────────────
class AgencyCreate(BaseModel):
    name: str
    slug: str


class AgencyOut(ORMModel):
    id: int
    name: str
    slug: str
    created_at: datetime | None = None


class AgencyAdminOut(ORMModel):
    """Agency row for the operator console, with a live recruiter count."""
    id: int
    name: str
    slug: str
    recruiter_count: int = 0
    created_at: datetime | None = None


# ── Recruiter auth ────────────────────────────────────────────────────────
class RecruiterLoginRequest(BaseModel):
    email: EmailStr
    password: str


class RecruiterRefreshRequest(BaseModel):
    refresh_token: str


class RecruiterTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RecruiterMe(ORMModel):
    id: int
    email: str
    full_name: str | None
    role: RecruiterSeatRole
    agency: AgencyOut


# ── Recruiter management (admin-only) ─────────────────────────────────────
class RecruiterCreate(BaseModel):
    agency_id: int
    email: EmailStr
    full_name: str | None = None
    password: str = Field(min_length=8)
    role: RecruiterSeatRole = RecruiterSeatRole.recruiter


class RecruiterUpdate(BaseModel):
    full_name: str | None = None
    is_active: bool | None = None
    role: RecruiterSeatRole | None = None


class RecruiterPasswordReset(BaseModel):
    password: str = Field(min_length=8)


class RecruiterAdminOut(ORMModel):
    id: int
    agency_id: int
    agency_name: str | None = None
    email: str
    full_name: str | None
    role: RecruiterSeatRole
    is_active: bool
    created_at: datetime | None = None


# ── Role ──────────────────────────────────────────────────────────────────
class RoleCreate(BaseModel):
    title: str
    description: str | None = None
    client_id: int | None = None
    status: RoleStatus = RoleStatus.open
    employment_type: EmploymentType | None = None
    location: str | None = None
    seniority: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    min_years_experience: float | None = None
    salary_min: int | None = None
    salary_max: int | None = None


class RoleOut(ORMModel):
    id: int
    agency_id: int
    client_id: int | None
    title: str
    description: str | None
    status: RoleStatus
    employment_type: EmploymentType | None
    location: str | None
    seniority: str | None
    required_skills: list[str]
    preferred_skills: list[str]
    min_years_experience: float | None
    salary_min: int | None
    salary_max: int | None


# ── Candidate ─────────────────────────────────────────────────────────────
class CandidateSkillOut(ORMModel):
    name: str


class CandidateOut(ORMModel):
    id: int
    agency_id: int
    full_name: str | None
    email: str | None
    phone: str | None
    headline: str | None
    location: str | None
    years_experience: float | None
    summary: str | None
    skills: list[CandidateSkillOut] = Field(default_factory=list)


class IngestResultItem(BaseModel):
    candidate_id: int
    full_name: str | None
    email: str | None
    skill_count: int


class IngestResult(BaseModel):
    ingested: int
    candidates: list[IngestResultItem]


# ── Shortlist / matching ──────────────────────────────────────────────────
class ShortlistEntryOut(ORMModel):
    candidate_id: int
    rank: int
    fit_score: float
    reasons: list[str]
    gaps: list[str]
    score_breakdown: dict


class ShortlistOut(ORMModel):
    id: int
    role_id: int
    created_at: datetime | None = None
    entries: list[ShortlistEntryOut] = Field(default_factory=list)


# ── Application (tracking) ─────────────────────────────────────────────────
class ApplicationCreate(BaseModel):
    candidate_id: int
    role_id: int | None = None
    company_name: str | None = None
    job_title: str | None = None
    stage: ApplicationStage = ApplicationStage.sourced
    notes: str | None = None


class ApplicationStageUpdate(BaseModel):
    stage: ApplicationStage


class ApplicationOut(ORMModel):
    id: int
    candidate_id: int
    role_id: int | None
    company_name: str | None
    job_title: str | None
    stage: ApplicationStage
    notes: str | None
    last_activity_at: datetime | None = None

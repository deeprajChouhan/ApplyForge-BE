from datetime import date, datetime
import json
import re
from typing import Any
from pydantic import BaseModel, field_validator


def parse_flexible_date(v: any) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        if isinstance(v, datetime):
            return v.date()
        return v
    if isinstance(v, str):
        v_lower = v.strip().lower()
        if v_lower in ("present", "now", "current", ""):
            return None
        
        v_clean = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', v.strip())
        formats = [
            "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", 
            "%B %d %Y", "%b %d %Y", "%B %Y", "%b %Y", "%Y"
        ]
        for fmt in formats:
            try:
                return datetime.strptime(v_clean, fmt).date()
            except ValueError:
                pass
        # Fallback to None if unparseable
        return None
    return v


class UserProfileUpsert(BaseModel):
    full_name: str | None = None
    headline: str | None = None
    summary: str | None = None
    location: str | None = None
    phone_number: str | None = None
    age: int | None = None


def _parse_json_list(v: Any) -> list:
    if not v:
        return []
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return []
    return v


class UserProfileOut(UserProfileUpsert):
    id: int
    user_id: int
    onboarding_completed: bool = False
    current_role: str | None = None
    career_goals: str | None = None
    target_roles: list[str] = []
    preferred_locations: list[str] = []
    salary_expectation: str | None = None
    deal_breakers: list[str] = []
    model_config = {"from_attributes": True}

    @field_validator('target_roles', 'preferred_locations', 'deal_breakers', mode='before')
    @classmethod
    def parse_json_list_fields(cls, v):
        return _parse_json_list(v)


class OnboardingRequest(BaseModel):
    """Answers collected by the conversational onboarding flow."""
    current_role: str | None = None
    career_goals: str
    target_roles: list[str] = []
    preferred_locations: list[str] = []
    salary_expectation: str | None = None
    deal_breakers: list[str] | None = None


class ExperienceIn(BaseModel):
    company: str
    role: str
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None

    @field_validator('start_date', 'end_date', mode='before')
    @classmethod
    def parse_date_fields(cls, v):
        return parse_flexible_date(v)


class ExperienceOut(ExperienceIn):
    id: int
    user_id: int
    model_config = {"from_attributes": True}


class EducationIn(BaseModel):
    institution: str
    degree: str | None = None
    field_of_study: str | None = None
    start_date: date | None = None
    end_date: date | None = None

    @field_validator('start_date', 'end_date', mode='before')
    @classmethod
    def parse_date_fields(cls, v):
        return parse_flexible_date(v)


class EducationOut(EducationIn):
    id: int
    user_id: int
    model_config = {"from_attributes": True}


class ProjectIn(BaseModel):
    name: str
    description: str | None = None
    technologies: str | None = None


class ProjectOut(ProjectIn):
    id: int
    user_id: int
    model_config = {"from_attributes": True}


class SkillIn(BaseModel):
    name: str
    level: str | None = None


class SkillOut(SkillIn):
    id: int
    user_id: int
    model_config = {"from_attributes": True}


class CertificationIn(BaseModel):
    name: str
    issuer: str | None = None
    issue_date: date | None = None

    @field_validator('issue_date', mode='before')
    @classmethod
    def parse_date_fields(cls, v):
        return parse_flexible_date(v)


class CertificationOut(CertificationIn):
    id: int
    user_id: int
    model_config = {"from_attributes": True}


class ResumeParseResponse(BaseModel):
    parse_id: int
    confidence_score: float
    structured_data: dict


# ---------------------------------------------------------------------------
# LinkedIn connections (Phase 2)
# ---------------------------------------------------------------------------

class LinkedInConnectionOut(BaseModel):
    """Single LinkedIn connection as returned by the API."""
    id: int
    full_name: str
    company: str | None = None
    position: str | None = None
    connected_on: date | None = None

    model_config = {"from_attributes": True}


class LinkedInImportResponse(BaseModel):
    """Summary returned after a CSV ingestion."""
    imported: int            # newly inserted rows
    updated: int             # rows that already existed and were refreshed
    total: int               # total rows processed from the CSV
    applications_refreshed: int  # applications whose priority_score was recomposed

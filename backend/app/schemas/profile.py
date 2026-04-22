from datetime import date
from pydantic import BaseModel


class UserProfileUpsert(BaseModel):
    full_name: str | None = None
    headline: str | None = None
    summary: str | None = None
    location: str | None = None


class UserProfileOut(UserProfileUpsert):
    id: int
    user_id: int
    model_config = {"from_attributes": True}


class ExperienceIn(BaseModel):
    company: str
    role: str
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None


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


class CertificationOut(CertificationIn):
    id: int
    user_id: int
    model_config = {"from_attributes": True}


class ResumeParseResponse(BaseModel):
    parse_id: int
    confidence_score: float
    structured_data: dict

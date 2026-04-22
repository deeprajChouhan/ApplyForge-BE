from datetime import datetime
from pydantic import BaseModel
from app.models.enums import ApplicationStatus, DocumentType


class ApplicationCreate(BaseModel):
    company_name: str
    role_title: str
    job_description: str


class ApplicationUpdate(BaseModel):
    company_name: str | None = None
    role_title: str | None = None
    job_description: str | None = None


class ApplicationOut(BaseModel):
    id: int
    company_name: str
    role_title: str
    job_description: str
    status: ApplicationStatus
    jd_analysis_json: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class StatusChangeRequest(BaseModel):
    status: ApplicationStatus
    note: str | None = None


class JDAnalyzeRequest(BaseModel):
    job_description: str


class JDAnalyzeResponse(BaseModel):
    keywords: list[str]
    required_skills: list[str]
    preferred_skills: list[str]
    strengths: list[str]
    unsupported_gaps: list[str]
    fit_summary: str


class GenerateRequest(BaseModel):
    doc_types: list[DocumentType]


class GeneratedDocumentOut(BaseModel):
    id: int
    application_id: int
    doc_type: DocumentType
    version: int
    content: str
    format: str

    model_config = {"from_attributes": True}

from datetime import datetime
from pydantic import BaseModel
from app.models.enums import ApplicationStatus, DocumentType


class SetResumeRequest(BaseModel):
    """
    Body for PATCH /applications/{id}/resume.
    Set parsed_resume_id=null to revert to latest-resume (default) behaviour.
    """
    parsed_resume_id: int | None = None


class ApplicationCreate(BaseModel):
    company_name: str
    role_title: str
    job_description: str
    jd_link: str | None = None


class ApplicationUpdate(BaseModel):
    company_name: str | None = None
    role_title: str | None = None
    job_description: str | None = None
    jd_link: str | None = None


class ApplicationOut(BaseModel):
    id: int
    company_name: str
    role_title: str
    job_description: str
    jd_link: str | None = None
    status: ApplicationStatus
    jd_analysis_json: str | None = None
    fit_score: float | None = None
    competition_score: float | None = None
    priority_score: float | None = None
    # PRO: which parsed resume is selected for this application (None = use latest)
    selected_resume_id: int | None = None
    # RCMS match_score set at queue-time by the auto-apply orchestrator.
    # Prefer this over `fit_score` for the drawer's match badge so the
    # queue row and drawer stay in sync.
    match_score: int | None = None
    # Auto-apply lifecycle stage — surfaced on ApplicationOut so the
    # drawer's StagePill can render without a separate fetch.
    auto_apply_stage: str | None = None
    # Optional job metadata mirrored from the linked Job row (nullable
    # because manually-created applications may not have them).
    location: str | None = None
    work_type: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApplicationListResponse(BaseModel):
    items: list[ApplicationOut]
    total: int
    page: int
    page_size: int


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


class GenerateResponse(BaseModel):
    status: str
    documents: list[GeneratedDocumentOut]


class PackageResponse(BaseModel):
    """Response for POST /applications/{id}/package — resume + cover letter + cold email in one call."""
    status: str
    documents: list[GeneratedDocumentOut]
    packages_used_this_month: int
    monthly_package_limit: int  # -1 = unlimited


class ScoreResponse(BaseModel):
    priority_score: float
    fit_score: float
    competition_score: float
    fit_breakdown: dict[str, float] = {}
    recommendation: str
    label: str
    # Enriched preview fields (populated by /utils/score-preview)
    job_summary: str = ""
    key_requirements: list[str] = []
    why_score: str = ""
    reply_probability: float = 0.0
    reply_label: str = ""
    reply_reasoning: str = ""
    required_yoe: int | None = None
    detected_seniority: str | None = None
    work_type: str = ""
    contract_type: str = ""


class ScorePreviewRequest(BaseModel):
    """
    Body for POST /utils/score-preview (browser extension endpoint).
    Scores a JD without a saved application -- no RAG, no profile lookup.
    """
    jd_text: str
    company_name: str = ""
    role_title: str = ""


class CoverLetterExportRequest(BaseModel):
    """
    Recipient details collected before exporting a cover letter.
    The backend merges these with the user's own profile data (name, email,
    phone, location) pulled from the DB / parsed resume so every [placeholder]
    in the generated content is replaced with real information.
    """
    recipient_name: str = "Hiring Manager"
    recipient_title: str | None = None
    company_address: str | None = None
    export_format: str = "pdf"   # "pdf" | "docx"

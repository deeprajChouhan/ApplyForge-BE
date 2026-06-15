from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_feature
from app.db.session import get_db
from app.models.enums import ApplicationStatus, FeatureFlag
from app.models.models import User
from app.schemas.application import (
    ApplicationCreate,
    ApplicationListResponse,
    ApplicationOut,
    ApplicationUpdate,
    GenerateRequest,
    GenerateResponse,
    GeneratedDocumentOut,
    JDAnalyzeRequest,
    PackageResponse,
    ScoreResponse,
    SetResumeRequest,
    StatusChangeRequest,
)
from app.schemas.profile import LinkedInConnectionOut
from app.schemas.suggestions import ApplySuggestionRequest, CustomizationOut, SuggestionsResponse
from app.services.linkedin.service import LinkedInService
from app.services.applications.service import ApplicationService
from app.services.suggestions.service import SuggestionService
from app.services.export.resume_exporter import ResumeExporter

router = APIRouter(prefix="/applications", tags=["applications"])

_need_apps = Depends(require_feature(FeatureFlag.applications))
_need_kanban = Depends(require_feature(FeatureFlag.kanban))
_need_jd = Depends(require_feature(FeatureFlag.jd_analyze))
_need_package = Depends(require_feature(FeatureFlag.package_generation))


@router.post("", response_model=ApplicationOut, dependencies=[_need_apps])
def create(payload: ApplicationCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ApplicationService(db, user.id).create(payload.model_dump())


@router.get("", response_model=ApplicationListResponse, dependencies=[_need_apps])
def list_apps(
    status: ApplicationStatus | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items, total = ApplicationService(db, user.id).list_paginated(status, search, page, page_size)
    return ApplicationListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/kanban", dependencies=[_need_kanban])
def kanban(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = ApplicationService(db, user.id)
    result = {}
    for s in ApplicationStatus:
        result[s.value] = [ApplicationOut.model_validate(a).model_dump() for a in service.list(s)]
    return result


@router.get("/{app_id}", response_model=ApplicationOut, dependencies=[_need_apps])
def get_app(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ApplicationService(db, user.id).get(app_id)


@router.patch("/{app_id}", response_model=ApplicationOut, dependencies=[_need_apps])
def update(app_id: int, payload: ApplicationUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ApplicationService(db, user.id).update(app_id, payload.model_dump())


@router.patch("/{app_id}/resume", response_model=ApplicationOut, dependencies=[_need_apps])
def set_resume(app_id: int, payload: SetResumeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Pin (or unpin) a specific parsed resume to this application."""
    return ApplicationService(db, user.id).set_resume(app_id, payload.parsed_resume_id)


@router.delete("/{app_id}", status_code=204, dependencies=[_need_apps])
def delete_application(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ApplicationService(db, user.id).delete(app_id)


@router.post("/{app_id}/status", response_model=ApplicationOut, dependencies=[_need_apps])
def change_status(app_id: int, payload: StatusChangeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ApplicationService(db, user.id).change_status(app_id, payload.status, payload.note)


@router.post("/{app_id}/analyze", dependencies=[_need_jd])
def analyze_jd(app_id: int, payload: JDAnalyzeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ApplicationService(db, user.id).analyze_jd(app_id, payload.job_description)


@router.post("/{app_id}/score", response_model=ScoreResponse, dependencies=[_need_jd])
def score_application(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ApplicationService(db, user.id).compute_priority_score(app_id)


@router.get("/{app_id}/connections", response_model=list[LinkedInConnectionOut], dependencies=[_need_apps])
def get_application_connections(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    app = ApplicationService(db, user.id).get(app_id)
    svc = LinkedInService(db, user.id)
    return svc.get_connections_for_company(app.company_name)


@router.post("/{app_id}/generate", response_model=GenerateResponse, dependencies=[_need_jd])
def generate(app_id: int, payload: GenerateRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    docs = ApplicationService(db, user.id).generate_docs(app_id, payload.doc_types)
    return GenerateResponse(
        status="completed",
        documents=[GeneratedDocumentOut.model_validate(doc) for doc in docs],
    )


@router.post("/{app_id}/package", response_model=PackageResponse, dependencies=[_need_package])
def generate_package(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Generate a complete application package: resume + cover letter + cold email in one call."""
    docs, used, limit = ApplicationService(db, user.id).generate_package(app_id)
    return PackageResponse(
        status="completed",
        documents=[GeneratedDocumentOut.model_validate(doc) for doc in docs],
        packages_used_this_month=used,
        monthly_package_limit=limit,
    )


@router.post("/{app_id}/suggestions", response_model=SuggestionsResponse, dependencies=[_need_jd])
def generate_suggestions(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Generate AI-powered resume improvement suggestions for this application."""
    return SuggestionService(db, user.id).generate(app_id)


@router.get("/{app_id}/customizations", response_model=CustomizationOut, dependencies=[_need_apps])
def get_customizations(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return per-application AI customizations (applied suggestions count + data)."""
    return SuggestionService(db, user.id).get_customizations(app_id)


@router.post("/{app_id}/suggestions/apply", response_model=CustomizationOut, dependencies=[_need_jd])
def apply_suggestion(app_id: int, payload: ApplySuggestionRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Apply a single AI suggestion as a per-application override (does not mutate the global profile)."""
    return SuggestionService(db, user.id).apply_suggestion(app_id, payload.suggestion)


@router.get("/{app_id}/documents/current", dependencies=[_need_apps])
def get_current_documents(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.models import GeneratedDocument
    from app.models.enums import DocumentType
    ApplicationService(db, user.id).get(app_id)
    result = {}
    for dt in DocumentType:
        doc = (
            db.query(GeneratedDocument)
            .filter_by(application_id=app_id, doc_type=dt)
            .order_by(GeneratedDocument.version.desc())
            .first()
        )
        if doc:
            result[dt.value] = GeneratedDocumentOut.model_validate(doc).model_dump()
        else:
            result[dt.value] = None
    return result


@router.get("/{app_id}/export/pdf", dependencies=[_need_apps])
def export_resume_pdf(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Download the user's resume as an ATS-optimised PDF."""
    # Verify the application belongs to this user (raises 404 otherwise)
    ApplicationService(db, user.id).get(app_id)
    pdf_bytes = ResumeExporter(db, user, app_id=app_id).as_pdf()
    safe_name = (user.email or "resume").split("@")[0].replace(" ", "_")
    filename = f"{safe_name}_resume.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{app_id}/export/docx", dependencies=[_need_apps])
def export_resume_docx(app_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Download the user's resume as a DOCX file."""
    ApplicationService(db, user.id).get(app_id)
    docx_bytes = ResumeExporter(db, user, app_id=app_id).as_docx()
    safe_name = (user.email or "resume").split("@")[0].replace(" ", "_")
    filename = f"{safe_name}_resume.docx"
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

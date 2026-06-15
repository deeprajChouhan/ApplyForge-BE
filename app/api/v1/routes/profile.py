import json
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_feature
from app.db.session import get_db
from app.models.enums import FeatureFlag
from app.models.models import User
from app.schemas.profile import (
    CertificationIn,
    EducationIn,
    ExperienceIn,
    LinkedInConnectionOut,
    LinkedInImportResponse,
    OnboardingRequest,
    ProjectIn,
    ResumeParseResponse,
    SkillIn,
    UserProfileOut,
    UserProfileUpsert,
)
from app.services.linkedin.service import LinkedInService, parse_linkedin_csv
from app.services.parsing.service import ResumeParsingService
from app.services.profile.service import PROFILE_MODELS, ProfileService, delete_owned, list_owned, upsert_owned
from app.services.rag.service import RAGService
from app.services.storage.service import get_storage_service

router = APIRouter(prefix="/profile", tags=["profile"])
_need_resume = Depends(require_feature(FeatureFlag.resume))


@router.get("", response_model=UserProfileOut)
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ProfileService(db, user.id).get_or_create_profile()


@router.put("", response_model=UserProfileOut)
def update_profile(payload: UserProfileUpsert, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ProfileService(db, user.id).update_profile(payload.model_dump())


@router.post("/onboarding", response_model=UserProfileOut)
def complete_onboarding(
    payload: OnboardingRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Persist the conversational onboarding answers and mark onboarding as complete."""
    return ProfileService(db, user.id).complete_onboarding(payload.model_dump(), request=request)


# ---------------------------------------------------------------------------
# LinkedIn connections (Phase 2) -- defined BEFORE the /{section} wildcard so
# Starlette resolves the literal path "linkedin-connections" with priority.
# ---------------------------------------------------------------------------

@router.post(
    "/linkedin-connections",
    response_model=LinkedInImportResponse,
    summary="Import LinkedIn Connections CSV",
    description=(
        "Upload the Connections.csv file exported from LinkedIn "
        "(Settings > Data privacy > Get a copy of your data). "
        "Rows are upserted by (user_id, full_name); re-importing refreshes "
        "company/position without creating duplicates. "
        "After ingestion, priority_score is recomposed for every saved "
        "application where JD analysis already exists."
    ),
)
async def import_linkedin_connections(
    file: UploadFile = File(..., description="LinkedIn Connections.csv export"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LinkedInImportResponse:
    # --- validate content-type loosely (CSV can arrive as text/plain or text/csv)
    if file.content_type and "html" in file.content_type:
        raise HTTPException(
            status_code=415,
            detail="Expected a CSV file. Received an HTML response — make sure you are uploading the raw Connections.csv file.",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # --- parse CSV
    try:
        rows = parse_linkedin_csv(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not rows:
        raise HTTPException(
            status_code=422,
            detail="No connection rows found in the CSV. Verify you uploaded the correct file.",
        )

    svc = LinkedInService(db, user.id)

    # --- upsert connections
    upsert_result = svc.upsert_connections(rows)

    # --- recompose priority scores for all existing applications
    apps_refreshed = svc.refresh_all_priority_scores()

    return LinkedInImportResponse(
        imported=upsert_result["imported"],
        updated=upsert_result["updated"],
        total=upsert_result["total"],
        applications_refreshed=apps_refreshed,
    )


@router.get(
    "/linkedin-connections",
    response_model=list[LinkedInConnectionOut],
    summary="List LinkedIn connections",
)
def list_linkedin_connections(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LinkedInConnectionOut]:
    """Return all LinkedIn connections stored for the current user."""
    from app.models.models import LinkedInConnection
    conns = (
        db.query(LinkedInConnection)
        .filter_by(user_id=user.id)
        .order_by(LinkedInConnection.full_name)
        .all()
    )
    return conns


# ---------------------------------------------------------------------------
# Generic profile section CRUD (wildcard -- must come AFTER named routes)
# ---------------------------------------------------------------------------

def _payload_by_section(section: str, payload: dict):
    match section:
        case "experiences":
            return ExperienceIn(**payload).model_dump()
        case "educations":
            return EducationIn(**payload).model_dump()
        case "projects":
            return ProjectIn(**payload).model_dump()
        case "skills":
            return SkillIn(**payload).model_dump()
        case "certifications":
            return CertificationIn(**payload).model_dump()
    raise HTTPException(status_code=404, detail="Unknown section")


@router.get("/{section}")
def list_section(section: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    model = PROFILE_MODELS.get(section)
    if not model:
        raise HTTPException(status_code=404, detail="Unknown section")
    return list_owned(db, model, user.id)


@router.post("/{section}")
def create_section(section: str, payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    model = PROFILE_MODELS.get(section)
    if not model:
        raise HTTPException(status_code=404, detail="Unknown section")
    return upsert_owned(db, model, user.id, None, _payload_by_section(section, payload))


@router.put("/{section}/{item_id}")
def update_section(section: str, item_id: int, payload: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    model = PROFILE_MODELS.get(section)
    if not model:
        raise HTTPException(status_code=404, detail="Unknown section")
    return upsert_owned(db, model, user.id, item_id, _payload_by_section(section, payload))


@router.delete("/{section}/{item_id}")
def delete_section(section: str, item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    model = PROFILE_MODELS.get(section)
    if not model:
        raise HTTPException(status_code=404, detail="Unknown section")
    delete_owned(db, model, user.id, item_id)
    return {"message": "deleted"}


@router.get("/resume/history", dependencies=[_need_resume])
def resume_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.models import ParsedResumeData, UploadedFile
    rows = (
        db.query(ParsedResumeData, UploadedFile.filename)
        .join(UploadedFile, ParsedResumeData.uploaded_file_id == UploadedFile.id, isouter=True)
        .filter(ParsedResumeData.user_id == user.id)
        .filter(ParsedResumeData.deleted_at.is_(None))
        .order_by(ParsedResumeData.created_at.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "parse_id": row.id,
            "file_id": row.uploaded_file_id,
            "filename": filename,
            "confidence_score": row.confidence_score,
            "parsed_at": row.created_at.isoformat(),
            "structured_data": json.loads(row.structured_json),
        }
        for row, filename in rows
    ]


@router.post("/resume/upload", dependencies=[_need_resume])
async def upload_resume(file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = await ResumeParsingService(db, user.id).save_upload(file)
    return {"file_id": row.id, "filename": row.filename}


@router.post("/resume/{file_id}/parse", response_model=ResumeParseResponse, dependencies=[_need_resume])
def parse_resume(file_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    parsed = ResumeParsingService(db, user.id).parse_resume(file_id)
    return ResumeParseResponse(parse_id=parsed.id, confidence_score=parsed.confidence_score, structured_data=json.loads(parsed.structured_json))


@router.delete("/resume/upload/{file_id}", status_code=204, dependencies=[_need_resume])
def delete_uploaded_file(file_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Delete an uploaded resume file from storage and the database."""
    from app.models.models import UploadedFile
    f = db.query(UploadedFile).filter_by(id=file_id, user_id=user.id).first()
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    get_storage_service().delete_file(f.path)
    db.delete(f)
    db.commit()


@router.delete("/resume/parsed/{parse_id}", status_code=204, dependencies=[_need_resume])
def delete_parsed_resume(parse_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Archive a parsed resume record. It is hidden from history but can be restored by an admin."""
    from app.models.models import ParsedResumeData
    from app.services.audit.service import AuditService
    from app.services.common.soft_delete import soft_delete

    row = (
        db.query(ParsedResumeData)
        .filter_by(id=parse_id, user_id=user.id)
        .filter(ParsedResumeData.deleted_at.is_(None))
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Parsed resume not found")
    soft_delete(db, row, deleted_by=user.id)
    AuditService(db).log(
        action="resume_archived",
        entity_type="parsed_resume_data",
        entity_id=row.id,
        actor_user_id=user.id,
        actor_role="user",
    )


@router.post("/knowledge/rebuild", dependencies=[_need_resume])
def rebuild_knowledge(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    count = RAGService(db, user.id).rebuild_index()
    return {"chunks_indexed": count}

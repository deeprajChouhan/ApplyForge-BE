"""Sourced stage routes — /agencies/{agency_id}/sourced.

Capture from the Chrome extension lands here first (with a fit score for the
role being sourced for); recruiters vet, attach documents, then promote into
the candidate pool and optionally a role pipeline. See services/sourcing.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.recruiter.api.deps import get_agency, require_unlocked_agency
from app.recruiter.enums import UsageKind
from app.recruiter.ids import PublicIdRoute
from app.recruiter.models import Agency, Role, SourcedProfile
from app.recruiter.schemas import (
    FitScoreOut,
    SourcedCaptureRequest,
    SourcedDetailOut,
    SourcedDocumentOut,
    SourcedOut,
    SourcedPromoteRequest,
    SourcedPromoteResult,
    SourcedSaveResult,
    SourcedUpdate,
)
from app.recruiter.services import documents as documents_service
from app.recruiter.services import sourcing
from app.recruiter.services import usage as usage_service

router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/agencies/{agency_id}/sourced",
    tags=["recruiter: sourced"],
)


def tier_for(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 75:
        return "strong"
    if score >= 50:
        return "good"
    return "low"


def _role(db: Session, agency: Agency, role_id: int | None) -> Role | None:
    if role_id is None:
        return None
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found for this agency")
    return role


def _sourced_or_404(db: Session, agency: Agency, sourced_id: int) -> SourcedProfile:
    sp = sourcing.get_sourced(db, agency.id, sourced_id)
    if sp is None:
        raise HTTPException(status_code=404, detail="Sourced profile not found")
    return sp


def _role_title(db: Session, sp: SourcedProfile) -> str | None:
    if not sp.role_id:
        return None
    role = db.get(Role, sp.role_id)
    return role.title if role else None


def _out(db: Session, sp: SourcedProfile) -> SourcedOut:
    return SourcedOut(
        id=sp.id,
        status=sp.status,
        role_id=sp.role_id,
        role_title=_role_title(db, sp),
        linkedin_url=sp.linkedin_url,
        full_name=sp.full_name,
        headline=sp.headline,
        location=sp.location,
        email=sp.email,
        phone=sp.phone,
        years_experience=sp.years_experience,
        skills=sp.skills or [],
        fit_score=sp.fit_score,
        tier=tier_for(sp.fit_score),
        document_count=len(sp.documents),
        promoted_candidate_id=sp.promoted_candidate_id,
        created_at=sp.created_at,
        updated_at=sp.updated_at,
    )


def _doc_out(d) -> SourcedDocumentOut:
    return SourcedDocumentOut(
        id=d.id,
        kind=d.kind,
        original_filename=d.original_filename,
        content_type=d.content_type,
        size_bytes=d.size_bytes,
        has_text=bool(d.extracted_text),
        created_at=d.created_at,
    )


def _detail(db: Session, sp: SourcedProfile) -> SourcedDetailOut:
    base = _out(db, sp).model_dump()
    return SourcedDetailOut(
        **base,
        about=sp.about,
        experiences=sp.experiences or [],
        education=sp.education or [],
        certifications=sp.certifications or [],
        languages=sp.languages or [],
        notes=sp.notes,
        fit_reasons=sp.fit_reasons or [],
        fit_gaps=sp.fit_gaps or [],
        fit_breakdown=sp.fit_breakdown or {},
        scored_at=sp.scored_at,
        promoted_at=sp.promoted_at,
        documents=[_doc_out(d) for d in sp.documents],
    )


@router.post("/score-preview", response_model=FitScoreOut)
def score_preview(
    payload: SourcedCaptureRequest,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    """Fit score of a scraped LinkedIn profile against `role_id`. Stores
    nothing. Also reports whether the person is already sourced or pooled."""
    role = _role(db, agency, payload.role_id)
    if role is None:
        raise HTTPException(status_code=400, detail="role_id is required to score a profile")
    data = payload.model_dump()
    try:
        result = sourcing.score_preview(role, data)
    except sourcing.SourcingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    existing = sourcing.find_by_url(db, agency.id, data.get("linkedin_url"))
    return FitScoreOut(
        role_id=role.id,
        role_title=role.title,
        fit_score=result.fit_score,
        tier=tier_for(result.fit_score),
        reasons=result.reasons,
        gaps=result.gaps,
        breakdown=result.breakdown,
        sourced_id=existing.id if existing else None,
        sourced_status=existing.status if existing else None,
        pool_candidate_id=sourcing.find_pool_candidate_id(db, agency.id, data.get("linkedin_url")),
    )


@router.post("", response_model=SourcedSaveResult, status_code=201)
def save_sourced(
    payload: SourcedCaptureRequest,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """Add (or refresh) a scraped profile in Sourced. Dedups on canonical
    LinkedIn URL; scores against `role_id` when given."""
    role = _role(db, agency, payload.role_id)
    try:
        res = sourcing.save_sourced(db, agency.id, payload.model_dump(), role)
    except sourcing.SourcingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return SourcedSaveResult(created=res.created, sourced=_out(db, res.sourced))


@router.get("", response_model=list[SourcedOut])
def list_sourced(
    status: str | None = Query("sourced", description="sourced | promoted | archived | all"),
    role_id: int | None = None,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    if status not in sourcing.STATUSES and status not in ("all", None):
        raise HTTPException(status_code=400, detail="Unknown status")
    rows = sourcing.list_sourced(db, agency.id, None if status in ("all", None) else status, role_id)
    return [_out(db, sp) for sp in rows]


@router.get("/{sourced_id}", response_model=SourcedDetailOut)
def get_sourced(sourced_id: int, agency: Agency = Depends(get_agency), db: Session = Depends(get_db)):
    return _detail(db, _sourced_or_404(db, agency, sourced_id))


@router.patch("/{sourced_id}", response_model=SourcedDetailOut)
def update_sourced(
    sourced_id: int,
    body: SourcedUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    sp = _sourced_or_404(db, agency, sourced_id)
    try:
        sp = sourcing.update_sourced(
            db, sp, body.model_dump(exclude_unset=True), lambda rid: _role(db, agency, rid)
        )
    except sourcing.SourcingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _detail(db, sp)


@router.post("/{sourced_id}/rescore", response_model=SourcedDetailOut)
def rescore(sourced_id: int, agency: Agency = Depends(require_unlocked_agency), db: Session = Depends(get_db)):
    return _detail(db, sourcing.rescore(db, _sourced_or_404(db, agency, sourced_id)))


@router.delete("/{sourced_id}", status_code=204)
def delete_sourced(sourced_id: int, agency: Agency = Depends(require_unlocked_agency), db: Session = Depends(get_db)):
    """Permanently delete the sourced record and its stored files."""
    sp = _sourced_or_404(db, agency, sourced_id)
    try:
        sourcing.delete_sourced(db, sp)
    except documents_service.DocumentDeleteError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    return Response(status_code=204)


@router.post("/{sourced_id}/documents", response_model=SourcedDocumentOut, status_code=201)
async def upload_document(
    sourced_id: int,
    file: UploadFile = File(...),
    kind: str = Form("cv"),
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """Attach a CV / cover letter / other file. A CV's text is folded into the
    fit score straight away."""
    sp = _sourced_or_404(db, agency, sourced_id)
    content = await file.read(documents_service.MAX_DOCUMENT_BYTES + 1)
    try:
        doc = sourcing.add_document(db, sp, file.filename, content, kind)
    except (sourcing.SourcingError, documents_service.DocumentRejected) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    return _doc_out(doc)


@router.get("/{sourced_id}/documents/{document_id}/download")
def download_document(
    sourced_id: int,
    document_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    sp = _sourced_or_404(db, agency, sourced_id)
    doc = sourcing.get_document(db, sp, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        content = sourcing.read_document(doc)
    except Exception:
        raise HTTPException(status_code=404, detail="Document file is no longer available")
    return Response(
        content=content,
        media_type=doc.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": documents_service.content_disposition(doc.original_filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/{sourced_id}/documents/{document_id}", status_code=204)
def delete_document(
    sourced_id: int,
    document_id: int,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    sp = _sourced_or_404(db, agency, sourced_id)
    doc = sourcing.get_document(db, sp, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        sourcing.delete_document(db, sp, doc)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Stored file could not be deleted")
    return Response(status_code=204)


@router.post("/{sourced_id}/promote", response_model=SourcedPromoteResult)
def promote(
    sourced_id: int,
    body: SourcedPromoteRequest,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """Move into the candidate pool (merging any CV) and, with `role_id`,
    onto that role's pipeline at the first stage."""
    sp = _sourced_or_404(db, agency, sourced_id)
    role = _role(db, agency, body.role_id)
    try:
        res = sourcing.promote(db, sp, role)
    except sourcing.SourcingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if res.created:
        usage_service.record(db, agency.id, UsageKind.cv_ingested, 1)
    return SourcedPromoteResult(
        sourced_id=sp.id,
        candidate_id=res.candidate_id,
        created=res.created,
        application_id=res.application_id,
        documents_moved=res.documents_moved,
    )

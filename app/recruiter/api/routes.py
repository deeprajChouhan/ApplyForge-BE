"""Recruiter API routes (Phase 1). All agency-scoped routes enforce tenant
isolation through the get_agency dependency."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

import secrets

from app.db.session import get_db
from app.recruiter.api.deps import (
    RECRUITER_ACCESS,
    get_agency,
    oauth2_recruiter,
    require_agency_feature,
    require_unlocked_agency,
)
from app.recruiter.enums import ApplicationStage
from app.recruiter.models import (
    Agency,
    Application,
    ApplicationNote,
    CandidateProfile,
    Client,
    MarketSnapshot,
    Recruiter,
    Role,
    RoleFeedback,
    RoleShareToken,
    Shortlist,
    ShortlistEntry,
    SpecSheetTemplate,
)
from app.recruiter.schemas import (
    ApplicationCreate,
    ApplicationOut,
    ApplicationStageUpdate,
    ApplicationNoteCreate,
    ApplicationNoteOut,
    AskCandidateRequest,
    AskCandidateResult,
    AssignCandidatesRequest,
    AssignCandidatesResult,
    CandidateBudgetUpdate,
    CandidateDetailOut,
    CandidateOut,
    CandidateRoleMatchesOut,
    ClientAnalyticsOut,
    ClientCreate,
    ClientOut,
    ClientUpdate,
    ConvertRequest,
    ConvertResult,
    IngestResult,
    IngestResultItem,
    JobListingOut,
    LinkedInCaptureRequest,
    LinkedInCaptureResult,
    MarketCrawlResult,
    MarketOverviewOut,
    MarketSnapshotOut,
    NextHireAdvisoryOut,
    NextHireSuggestionOut,
    ParseJDRequest,
    ParseJDResult,
    PublicFeedbackCreate,
    PublicRoleView,
    PublicShortlistCandidate,
    RoleFeedbackOut,
    ScreeningQuestion,
    ScreeningQuestionsOut,
    RoleBoardColumn,
    RoleBoardOut,
    RoleCreate,
    RoleShareTokenOut,
    RoleMatchOut,
    RoleOut,
    RoleUpdate,
    ShortlistOut,
    SwotOut,
)
from app.recruiter.bridge import provision_candidate
from app.recruiter.enums import UsageKind
from app.recruiter.services import usage as usage_service
from app.recruiter.services.advisory import next_hire_advisory
from app.recruiter.services.client_analytics import compute_client_analytics
from app.recruiter.services.ingestion import ingest_batch
from app.recruiter.services.linkedin_capture import capture_linkedin_profile
from app.recruiter.services.listing import generate_listing
from app.recruiter.services.market import compute_market
from app.recruiter.services.market_crawler import crawl_agency_market, crawl_role_market
from app.recruiter.services.matching import embed_role
from app.recruiter.services.placement import rank_roles_for_candidate
from app.recruiter.services.shortlist import generate_shortlist
from app.recruiter.services.skills import normalize_skill
from app.recruiter.services.candidate_chat import ask_about_candidate
from app.recruiter.services.jd_parse import parse_jd
from app.recruiter.services.proposal_pdf import render_role_proposal_pdf
from app.recruiter.services.screening import draft_screening_questions
from app.recruiter.services.spec_sheet import (
    build_spec_sheet_docx,
    build_spec_sheet_pdf,
    filename_for,
    resolve_branding,
)
from app.recruiter.services.swot import compute_swot

from jose import JWTError, jwt
from app.core.config import settings


def _soft_recruiter(
    token: str | None = Depends(oauth2_recruiter),
    db: Session = Depends(get_db),
) -> Recruiter | None:
    """Best-effort recruiter attribution — returns None for operator callers or
    unauthenticated flows so notes still record with kind=note but no author."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != RECRUITER_ACCESS:
            return None
        rec = db.get(Recruiter, int(payload.get("sub")))
        return rec if rec and rec.is_active else None
    except (JWTError, ValueError):
        return None

# Agencies are created and listed via the operator/admin routes
# (app/recruiter/api/admin_routes.py); recruiters get their own agency from
# /recruiter/auth/me. There is intentionally no unauthenticated agency listing.

# ── Clients ──────────────────────────────────────────────────────────────
clients_router = APIRouter(prefix="/agencies/{agency_id}/clients", tags=["recruiter: clients"])


def _client_out(db: Session, client: Client) -> ClientOut:
    count = db.query(Role).filter(Role.client_id == client.id).count()
    return ClientOut(
        id=client.id,
        agency_id=client.agency_id,
        name=client.name,
        industry=client.industry,
        role_count=count,
        primary_contact_name=client.primary_contact_name,
        contact_email=client.contact_email,
        contact_phone=client.contact_phone,
        website=client.website,
        address=client.address,
        notes=client.notes,
    )


@clients_router.post("", response_model=ClientOut, status_code=201)
def create_client(
    payload: ClientCreate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    client = Client(agency_id=agency.id, name=payload.name, industry=payload.industry)
    db.add(client)
    db.commit()
    db.refresh(client)
    return _client_out(db, client)


@clients_router.get("", response_model=list[ClientOut])
def list_clients(agency: Agency = Depends(get_agency), db: Session = Depends(get_db)):
    clients = db.query(Client).filter(Client.agency_id == agency.id).order_by(Client.name).all()
    return [_client_out(db, c) for c in clients]


def _load_client(db: Session, agency: Agency, client_id: int) -> Client:
    client = db.get(Client, client_id)
    if client is None or client.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@clients_router.get("/{client_id}", response_model=ClientOut)
def get_client(client_id: int, agency: Agency = Depends(get_agency), db: Session = Depends(get_db)):
    return _client_out(db, _load_client(db, agency, client_id))


@clients_router.patch("/{client_id}", response_model=ClientOut)
def update_client(
    client_id: int,
    payload: ClientUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    client = _load_client(db, agency, client_id)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(client, k, v)
    db.commit()
    db.refresh(client)
    return _client_out(db, client)


@clients_router.get("/{client_id}/analytics", response_model=ClientAnalyticsOut)
def client_analytics(
    client_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    """Fulfilment metrics, pipeline health, top skills, and recent placements."""
    client = _load_client(db, agency, client_id)
    return ClientAnalyticsOut(**compute_client_analytics(db, client))


@clients_router.get("/{client_id}/next-hire", response_model=NextHireAdvisoryOut)
def client_next_hire(
    client_id: int,
    agency: Agency = Depends(require_agency_feature("advisory")),
    db: Session = Depends(get_db),
):
    """Advisory: infer this client's likely next hire from their roster + benchmarks."""
    client = _load_client(db, agency, client_id)
    advisory = next_hire_advisory(db, agency.id, client)
    usage_service.record(db, agency.id, UsageKind.advisory_run)
    return NextHireAdvisoryOut(
        client_id=advisory.client_id,
        client_name=advisory.client_name,
        roster_roles=advisory.roster_roles,
        suggestions=[
            NextHireSuggestionOut(
                title=s.title,
                rationale=s.rationale,
                skills=s.skills,
                pool_supply=s.pool_supply,
                confidence=s.confidence,
            )
            for s in advisory.suggestions
        ],
        seniority_note=advisory.seniority_note,
    )


# ── Roles ────────────────────────────────────────────────────────────────
roles_router = APIRouter(prefix="/agencies/{agency_id}/roles", tags=["recruiter: roles"])


def _normalize_skills(skills: list[str]) -> list[str]:
    seen, out = set(), []
    for s in skills:
        n = normalize_skill(s)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


@roles_router.post("", response_model=RoleOut, status_code=201)
def create_role(
    payload: RoleCreate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    role = Role(
        agency_id=agency.id,
        client_id=payload.client_id,
        title=payload.title,
        description=payload.description,
        status=payload.status,
        employment_type=payload.employment_type,
        location=payload.location,
        seniority=payload.seniority,
        required_skills=_normalize_skills(payload.required_skills),
        preferred_skills=_normalize_skills(payload.preferred_skills),
        min_years_experience=payload.min_years_experience,
        salary_min=payload.salary_min,
        salary_max=payload.salary_max,
        budget_min=payload.budget_min,
        budget_max=payload.budget_max,
        budget_currency=payload.budget_currency or "USD",
        is_draft=payload.is_draft,
        notes=payload.notes,
    )
    db.add(role)
    db.flush()
    role.embedding = embed_role(role)
    db.commit()
    db.refresh(role)
    return role


@roles_router.patch("/{role_id}", response_model=RoleOut)
def update_role(
    role_id: int,
    payload: RoleUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """Partial-update. Re-embeds if any signal used by the vector changed."""
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found")

    reembed = False
    data = payload.model_dump(exclude_unset=True)
    if "required_skills" in data:
        data["required_skills"] = _normalize_skills(data["required_skills"] or [])
        reembed = True
    if "preferred_skills" in data:
        data["preferred_skills"] = _normalize_skills(data["preferred_skills"] or [])
        reembed = True
    for k in ("title", "description", "seniority"):
        if k in data:
            reembed = True

    for k, v in data.items():
        setattr(role, k, v)

    if reembed:
        role.embedding = embed_role(role)
    db.commit()
    db.refresh(role)
    return role


@roles_router.get("/{role_id}/proposal.pdf")
def role_proposal_pdf(
    role_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    """Client-safe proposal PDF for this role — signed-ready cover doc."""
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found")
    client = db.get(Client, role.client_id) if role.client_id else None
    pdf_bytes = render_role_proposal_pdf(role, agency, client)
    safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in (role.title or "role"))[:60]
    filename = f"{safe_title}-proposal.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@roles_router.post("/parse-jd", response_model=ParseJDResult)
def parse_jd_to_role(
    payload: ParseJDRequest,
    agency: Agency = Depends(require_unlocked_agency),  # noqa: ARG001 — agency-gate only
):
    """
    Turn a pasted JD or client email into a role-draft payload the frontend can
    pre-fill. Fail-soft: returns an empty draft if the LLM isn't configured.
    """
    return ParseJDResult(**parse_jd(payload.text))


@roles_router.get("/{role_id}/share", response_model=RoleShareTokenOut | None)
def get_role_share(
    role_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found")
    tok = (
        db.query(RoleShareToken)
        .filter(RoleShareToken.role_id == role.id, RoleShareToken.is_active.is_(True))
        .order_by(RoleShareToken.id.desc())
        .first()
    )
    if not tok:
        return None
    return _share_out(tok)


@roles_router.post("/{role_id}/share", response_model=RoleShareTokenOut, status_code=201)
def create_role_share(
    role_id: int,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """Mint (or rotate) a public share token. Any previous active token is revoked."""
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found")
    # Revoke any existing active tokens so the old URL stops working.
    (
        db.query(RoleShareToken)
        .filter(RoleShareToken.role_id == role.id, RoleShareToken.is_active.is_(True))
        .update({RoleShareToken.is_active: False})
    )
    tok = RoleShareToken(
        agency_id=agency.id,
        role_id=role.id,
        token=secrets.token_urlsafe(24),
        is_active=True,
    )
    db.add(tok)
    db.commit()
    db.refresh(tok)
    return _share_out(tok)


@roles_router.delete("/{role_id}/share", status_code=204)
def revoke_role_share(
    role_id: int,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found")
    (
        db.query(RoleShareToken)
        .filter(RoleShareToken.role_id == role.id, RoleShareToken.is_active.is_(True))
        .update({RoleShareToken.is_active: False})
    )
    db.commit()
    return None


def _share_out(tok: RoleShareToken) -> RoleShareTokenOut:
    return RoleShareTokenOut(
        id=tok.id,
        role_id=tok.role_id,
        token=tok.token,
        is_active=tok.is_active,
        view_count=tok.view_count,
        last_viewed_at=tok.last_viewed_at,
        created_at=tok.created_at,
        share_url=None,  # frontend appends origin
    )


@roles_router.post("/{role_id}/market", response_model=MarketSnapshotOut, status_code=201)
def refresh_market_snapshot(
    role_id: int,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """
    Run the market crawler for this role and cache the aggregate on the role
    itself. Used by the role-draft screen and the client-shareable view.
    """
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found")
    snap = crawl_role_market(db, agency.id, role=role)
    return snap


@roles_router.get("", response_model=list[RoleOut])
def list_roles(agency: Agency = Depends(get_agency), db: Session = Depends(get_db)):
    return db.query(Role).filter(Role.agency_id == agency.id).order_by(Role.id.desc()).all()


@roles_router.get("/{role_id}", response_model=RoleOut)
def get_role(role_id: int, agency: Agency = Depends(get_agency), db: Session = Depends(get_db)):
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


@roles_router.post("/{role_id}/listing", response_model=JobListingOut)
def draft_listing(
    role_id: int,
    agency: Agency = Depends(require_agency_feature("listings", write=True)),
    db: Session = Depends(get_db),
):
    """Draft a job listing for this role, grounded in the agency's pool patterns."""
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found")
    listing = generate_listing(db, role)
    usage_service.record(db, agency.id, UsageKind.listing_drafted)
    return JobListingOut(**listing.__dict__)


# ── Candidates + ingestion ───────────────────────────────────────────────
candidates_router = APIRouter(
    prefix="/agencies/{agency_id}/candidates", tags=["recruiter: candidates"]
)


@candidates_router.post("/ingest", response_model=IngestResult, status_code=201)
async def ingest_cvs(
    files: list[UploadFile] = File(...),
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """Bulk-CV ingestion: parse each uploaded CV into the agency's pool."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    payload: list[tuple[str, bytes]] = []
    for f in files:
        payload.append((f.filename or "cv.txt", await f.read()))

    ingested = ingest_batch(db, agency.id, payload)
    usage_service.record(db, agency.id, UsageKind.cv_ingested, len(ingested))
    return IngestResult(
        ingested=len(ingested),
        candidates=[
            IngestResultItem(
                candidate_id=i.candidate_id,
                full_name=i.full_name,
                email=i.email,
                skill_count=i.skill_count,
            )
            for i in ingested
        ],
    )


@candidates_router.post(
    "/capture-linkedin", response_model=LinkedInCaptureResult, status_code=201
)
def capture_linkedin(
    payload: LinkedInCaptureRequest,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """
    One-click LinkedIn capture from the recruiter Chrome extension.

    Deduplicates on canonical `linkedin_url` within the agency: re-capturing
    the same profile refreshes the pool copy in place (skills + experiences
    replaced from the fresh scrape) instead of creating a second row. When
    `role_id` is set, also attaches the candidate to that role's pipeline at
    stage `sourced` (idempotent per role).
    """
    role: Role | None = None
    if payload.role_id is not None:
        role = db.get(Role, payload.role_id)
        if role is None or role.agency_id != agency.id:
            raise HTTPException(status_code=404, detail="Role not found for this agency")

    try:
        result = capture_linkedin_profile(
            db,
            agency.id,
            payload.model_dump(exclude_none=False),
            role=role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Only meter the first-time capture — re-clicks that dedup shouldn't
    # double-charge an agency for the same profile.
    if result.created:
        usage_service.record(db, agency.id, UsageKind.cv_ingested, 1)

    return LinkedInCaptureResult(
        candidate_id=result.candidate_id,
        full_name=result.full_name,
        email=result.email,
        linkedin_url=result.linkedin_url,
        skill_count=result.skill_count,
        created=result.created,
        application_id=result.application_id,
    )


@candidates_router.get("", response_model=list[CandidateOut])
def list_candidates(agency: Agency = Depends(get_agency), db: Session = Depends(get_db)):
    return (
        db.query(CandidateProfile)
        .filter(CandidateProfile.agency_id == agency.id)
        .order_by(CandidateProfile.id.desc())
        .all()
    )


@candidates_router.get("/{candidate_id}", response_model=CandidateDetailOut)
def get_candidate(
    candidate_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    cand = db.get(CandidateProfile, candidate_id)
    if cand is None or cand.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return cand


def _load_export_context(
    db: Session,
    agency: Agency,
    candidate_id: int,
    role_id: int | None,
    template_id: int | None,
) -> tuple[CandidateProfile, Role | None, SpecSheetTemplate | None]:
    """
    Resolve + tenant-check the candidate, optional role, and optional
    template used by both export endpoints (.pdf / .docx). Extracted so
    both endpoints stay tiny and identical in their error surface.
    """
    cand = db.get(CandidateProfile, candidate_id)
    if cand is None or cand.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Candidate not found")

    role: Role | None = None
    if role_id is not None:
        role = db.get(Role, role_id)
        if role is None or role.agency_id != agency.id:
            raise HTTPException(status_code=404, detail="Role not found for this agency")

    template: SpecSheetTemplate | None = None
    effective_template_id = template_id if template_id is not None else agency.spec_sheet_template_id
    if effective_template_id:
        template = db.get(SpecSheetTemplate, effective_template_id)
        if template is None or template.agency_id != agency.id:
            raise HTTPException(status_code=404, detail="Spec-sheet template not found for this agency")
    return cand, role, template


def _resolve_anonymise(explicit: bool | None, template: SpecSheetTemplate | None) -> bool:
    """The `anonymise` query param overrides the template default when set."""
    if explicit is not None:
        return explicit
    if template is not None:
        return bool(template.anonymise_by_default)
    return False


@candidates_router.get("/{candidate_id}/spec-sheet.pdf")
def export_spec_sheet_pdf(
    candidate_id: int,
    anonymise: bool | None = Query(default=None),
    role_id: int | None = Query(default=None, ge=1),
    template_id: int | None = Query(default=None, ge=1),
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """
    Render this candidate as an agency-branded CV / spec-sheet PDF.

    - `anonymise=true` strips name/email/phone/exact companies; the resolved
      value is (query param) OR (template default) OR false.
    - `role_id` adds a "Fit against role" panel (matched skills + gaps).
    - `template_id` overrides the agency's default template.
    """
    cand, role, template = _load_export_context(db, agency, candidate_id, role_id, template_id)
    is_anon = _resolve_anonymise(anonymise, template)
    branding = resolve_branding(agency, template)
    pdf_bytes = build_spec_sheet_pdf(cand, agency, anonymise=is_anon, role=role, template=template)
    usage_service.record(db, agency.id, UsageKind.spec_sheet_exported, 1)
    fname = filename_for(cand, branding, extension="pdf", anonymise=is_anon)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@candidates_router.get("/{candidate_id}/spec-sheet.docx")
def export_spec_sheet_docx(
    candidate_id: int,
    anonymise: bool | None = Query(default=None),
    role_id: int | None = Query(default=None, ge=1),
    template_id: int | None = Query(default=None, ge=1),
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """
    Same content as spec-sheet.pdf, produced as an editable Word document
    so the recruiter can tweak wording before sending it to the client.
    """
    cand, role, template = _load_export_context(db, agency, candidate_id, role_id, template_id)
    is_anon = _resolve_anonymise(anonymise, template)
    branding = resolve_branding(agency, template)
    docx_bytes = build_spec_sheet_docx(cand, agency, anonymise=is_anon, role=role, template=template)
    usage_service.record(db, agency.id, UsageKind.spec_sheet_exported, 1)
    fname = filename_for(cand, branding, extension="docx", anonymise=is_anon)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@candidates_router.post("/{candidate_id}/convert", response_model=ConvertResult, status_code=201)
def convert_candidate(
    candidate_id: int,
    payload: ConvertRequest,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """
    Promote a CandidateProfile into a real ApplyForge consumer user (the one
    additive touchpoint). Requires explicit consent. One-way handoff: the profile
    is marked provisioned and the recruiter app stops driving those applications.
    """
    cand = db.get(CandidateProfile, candidate_id)
    if cand is None or cand.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if not payload.consent:
        raise HTTPException(status_code=400, detail="Candidate consent is required to convert a profile")
    if cand.provisioned_user_id is not None:
        raise HTTPException(status_code=409, detail="This profile has already been converted")

    email = (payload.email or cand.email or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="An email is required — the profile has none, so supply one")

    from app.services.provisioning.service import ProvisioningError

    try:
        user_id = provision_candidate(db, cand, email)
    except ProvisioningError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    cand.provisioned_user_id = user_id
    db.commit()
    db.refresh(cand)
    return ConvertResult(candidate_id=cand.id, provisioned_user_id=user_id, email=email)


@candidates_router.post("/{candidate_id}/ask", response_model=AskCandidateResult)
def ask_candidate(
    candidate_id: int,
    payload: AskCandidateRequest,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """
    Grounded QA over one candidate — CV + skills + experiences + activity
    notes + optional role context. Fail-soft: returns keyword-scan matches
    when the LLM isn't configured.
    """
    cand = db.get(CandidateProfile, candidate_id)
    if cand is None or cand.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Candidate not found")
    role: Role | None = None
    if payload.role_id is not None:
        role = db.get(Role, payload.role_id)
        if role is None or role.agency_id != agency.id:
            raise HTTPException(status_code=404, detail="Role not found for this agency")
    result = ask_about_candidate(db, cand, payload.question, role=role)
    return AskCandidateResult(**result)


@candidates_router.patch("/{candidate_id}/budget", response_model=CandidateOut)
def update_candidate_budget(
    candidate_id: int,
    payload: CandidateBudgetUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    cand = db.get(CandidateProfile, candidate_id)
    if cand is None or cand.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Candidate not found")
    cand.expected_budget_min = payload.expected_budget_min
    cand.expected_budget_max = payload.expected_budget_max
    cand.expected_budget_currency = payload.expected_budget_currency or "USD"
    db.commit()
    db.refresh(cand)
    return cand


@candidates_router.get("/{candidate_id}/role-matches", response_model=CandidateRoleMatchesOut)
def candidate_role_matches(
    candidate_id: int,
    include_closed: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1, le=100),
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    """Rank the agency's open roles by fit for this candidate (placement)."""
    cand = db.get(CandidateProfile, candidate_id)
    if cand is None or cand.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Candidate not found")
    matches = rank_roles_for_candidate(db, agency.id, cand, include_closed=include_closed, limit=limit)
    usage_service.record(db, agency.id, UsageKind.role_match_run)
    return CandidateRoleMatchesOut(
        candidate_id=candidate_id,
        matches=[
            RoleMatchOut(
                role_id=m.role_id,
                title=m.title,
                seniority=m.seniority,
                status=m.status,
                fit_score=m.fit_score,
                reasons=m.reasons,
                gaps=m.gaps,
                score_breakdown=m.breakdown,
            )
            for m in matches
        ],
    )


# ── Shortlist / matching ─────────────────────────────────────────────────
shortlist_router = APIRouter(
    prefix="/agencies/{agency_id}/roles/{role_id}/shortlist", tags=["recruiter: shortlists"]
)


def _load_role(db: Session, agency: Agency, role_id: int) -> Role:
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


@shortlist_router.post("", response_model=ShortlistOut, status_code=201)
def create_shortlist(
    role_id: int,
    limit: int | None = Query(default=None, ge=1, le=500),
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """Run inverted matching for this role and save the ranked shortlist."""
    role = _load_role(db, agency, role_id)
    shortlist = generate_shortlist(db, role, limit=limit)
    usage_service.record(db, agency.id, UsageKind.shortlist_generated)
    return shortlist


@shortlist_router.get("/latest", response_model=ShortlistOut)
def latest_shortlist(
    role_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    _load_role(db, agency, role_id)
    shortlist = (
        db.query(Shortlist)
        .filter(Shortlist.role_id == role_id, Shortlist.agency_id == agency.id)
        .order_by(Shortlist.id.desc())
        .first()
    )
    if shortlist is None:
        raise HTTPException(status_code=404, detail="No shortlist generated yet")
    return shortlist


# ── Applications (tracking-only) ─────────────────────────────────────────
applications_router = APIRouter(
    prefix="/agencies/{agency_id}/applications", tags=["recruiter: applications"]
)


@applications_router.post("", response_model=ApplicationOut, status_code=201)
def create_application(
    payload: ApplicationCreate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    cand = db.get(CandidateProfile, payload.candidate_id)
    if cand is None or cand.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Candidate not found")

    app_row = Application(
        agency_id=agency.id,
        candidate_id=payload.candidate_id,
        role_id=payload.role_id,
        company_name=payload.company_name,
        job_title=payload.job_title,
        stage=payload.stage,
        notes=payload.notes,
    )
    db.add(app_row)
    db.commit()
    db.refresh(app_row)
    return app_row


@applications_router.get("", response_model=list[ApplicationOut])
def list_applications(agency: Agency = Depends(get_agency), db: Session = Depends(get_db)):
    return (
        db.query(Application)
        .filter(Application.agency_id == agency.id)
        .order_by(Application.last_activity_at.desc())
        .all()
    )


@applications_router.patch("/{application_id}/stage", response_model=ApplicationOut)
def update_stage(
    application_id: int,
    payload: ApplicationStageUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    recruiter: Recruiter | None = Depends(_soft_recruiter),
    db: Session = Depends(get_db),
):
    app_row = db.get(Application, application_id)
    if app_row is None or app_row.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Application not found")
    prev_stage = app_row.stage
    app_row.stage = payload.stage
    app_row.last_activity_at = datetime.utcnow()
    # Auto-log the transition so the activity tab always tells the story of the
    # candidate's journey, even for stage moves the recruiter forgets to note.
    if prev_stage != payload.stage:
        db.add(
            ApplicationNote(
                agency_id=agency.id,
                application_id=app_row.id,
                author_recruiter_id=recruiter.id if recruiter else None,
                author_name=(recruiter.full_name or recruiter.email) if recruiter else None,
                kind="system",
                body=f"Stage moved {prev_stage.value} → {payload.stage.value}",
            )
        )
    db.commit()
    db.refresh(app_row)
    return app_row


# ── Application activity notes ──────────────────────────────────────────
@applications_router.get("/{application_id}/notes", response_model=list[ApplicationNoteOut])
def list_application_notes(
    application_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    app_row = db.get(Application, application_id)
    if app_row is None or app_row.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Application not found")
    return (
        db.query(ApplicationNote)
        .filter(ApplicationNote.application_id == application_id)
        .order_by(ApplicationNote.id.desc())
        .all()
    )


@applications_router.post("/{application_id}/notes", response_model=ApplicationNoteOut, status_code=201)
def create_application_note(
    application_id: int,
    payload: ApplicationNoteCreate,
    agency: Agency = Depends(require_unlocked_agency),
    recruiter: Recruiter | None = Depends(_soft_recruiter),
    db: Session = Depends(get_db),
):
    app_row = db.get(Application, application_id)
    if app_row is None or app_row.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Application not found")
    note = ApplicationNote(
        agency_id=agency.id,
        application_id=application_id,
        author_recruiter_id=recruiter.id if recruiter else None,
        author_name=(recruiter.full_name or recruiter.email) if recruiter else None,
        kind="note",
        body=payload.body.strip(),
    )
    db.add(note)
    app_row.last_activity_at = datetime.utcnow()
    db.commit()
    db.refresh(note)
    return note


@applications_router.delete("/{application_id}/notes/{note_id}", status_code=204)
def delete_application_note(
    application_id: int,
    note_id: int,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    note = db.get(ApplicationNote, note_id)
    if (
        note is None
        or note.agency_id != agency.id
        or note.application_id != application_id
    ):
        raise HTTPException(status_code=404, detail="Note not found")
    db.delete(note)
    db.commit()
    return None


# ── Role pipeline (Kanban board per role) ────────────────────────────────
pipeline_router = APIRouter(
    prefix="/agencies/{agency_id}/roles/{role_id}/pipeline", tags=["recruiter: pipeline"]
)


def _application_out(app_row: Application) -> ApplicationOut:
    return ApplicationOut.model_validate(app_row)


@pipeline_router.get("", response_model=RoleBoardOut)
def role_pipeline(
    role_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    """Kanban board: one column per stage, cards ordered by fit_score desc."""
    role = _load_role(db, agency, role_id)
    apps = (
        db.query(Application)
        .filter(Application.agency_id == agency.id, Application.role_id == role.id)
        .all()
    )
    by_stage: dict[ApplicationStage, list[Application]] = {s: [] for s in ApplicationStage}
    for a in apps:
        by_stage.setdefault(a.stage, []).append(a)
    columns = [
        RoleBoardColumn(
            stage=stage,
            applications=[
                _application_out(a)
                for a in sorted(
                    rows,
                    key=lambda r: (r.fit_score or -1, r.last_activity_at),
                    reverse=True,
                )
            ],
        )
        for stage, rows in by_stage.items()
    ]
    return RoleBoardOut(role_id=role.id, columns=columns, total=len(apps))


@pipeline_router.post("/assign", response_model=AssignCandidatesResult, status_code=201)
def assign_candidates_to_role(
    role_id: int,
    payload: AssignCandidatesRequest,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """
    Attach shortlisted candidates to this role's pipeline. Idempotent:
    candidates already in the pipeline are returned in `skipped_existing`.
    Caches fit_score on the Application if a shortlist_id is provided.
    """
    role = _load_role(db, agency, role_id)

    fit_by_cand: dict[int, float] = {}
    if payload.shortlist_id is not None:
        sl = db.get(Shortlist, payload.shortlist_id)
        if sl is None or sl.agency_id != agency.id or sl.role_id != role.id:
            raise HTTPException(status_code=404, detail="Shortlist not found for this role")
        for e in sl.entries:
            fit_by_cand[e.candidate_id] = e.fit_score

    existing_cand_ids = {
        a.candidate_id
        for a in db.query(Application)
        .filter(Application.agency_id == agency.id, Application.role_id == role.id)
        .all()
    }

    added_rows: list[Application] = []
    skipped: list[int] = []
    for cand_id in payload.candidate_ids:
        if cand_id in existing_cand_ids:
            skipped.append(cand_id)
            continue
        cand = db.get(CandidateProfile, cand_id)
        if cand is None or cand.agency_id != agency.id:
            skipped.append(cand_id)
            continue
        app_row = Application(
            agency_id=agency.id,
            candidate_id=cand_id,
            role_id=role.id,
            company_name=None,
            job_title=role.title,
            stage=payload.stage,
            fit_score=fit_by_cand.get(cand_id),
            added_from_shortlist_id=payload.shortlist_id,
        )
        db.add(app_row)
        added_rows.append(app_row)

    db.commit()
    for a in added_rows:
        db.refresh(a)
    return AssignCandidatesResult(
        added=[_application_out(a) for a in added_rows],
        skipped_existing=skipped,
    )


@applications_router.post("/{application_id}/screening-questions", response_model=ScreeningQuestionsOut)
def application_screening_questions(
    application_id: int,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """Generate 6-8 role-aware screening questions grounded on candidate gaps."""
    app_row = db.get(Application, application_id)
    if app_row is None or app_row.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Application not found")
    if app_row.role_id is None:
        raise HTTPException(status_code=400, detail="Application is not attached to a role")
    role = db.get(Role, app_row.role_id)
    cand = db.get(CandidateProfile, app_row.candidate_id)
    if role is None or cand is None:
        raise HTTPException(status_code=404, detail="Role or candidate missing")
    result = draft_screening_questions(role, cand, app_row)
    return ScreeningQuestionsOut(
        questions=[ScreeningQuestion(**q) for q in result["questions"]],
        generated_at=result["generated_at"],
        used_llm=result["used_llm"],
    )


@applications_router.post("/{application_id}/swot", response_model=SwotOut)
def application_swot(
    application_id: int,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """Generate (or regenerate) a role-aware SWOT for this candidate-in-role."""
    app_row = db.get(Application, application_id)
    if app_row is None or app_row.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Application not found")
    if app_row.role_id is None:
        raise HTTPException(status_code=400, detail="Application is not attached to a role")
    role = db.get(Role, app_row.role_id)
    cand = db.get(CandidateProfile, app_row.candidate_id)
    if role is None or cand is None:
        raise HTTPException(status_code=404, detail="Role or candidate missing")
    swot = compute_swot(role, cand, app_row)
    app_row.swot = swot
    db.commit()
    return SwotOut(**swot)


# ── Public share (unauthenticated, token-guarded) ────────────────────────
public_router = APIRouter(prefix="/public", tags=["recruiter: public"])


def _client_display_name(full_name: str | None, cid: int) -> str:
    """Client-facing name: keep first name only for privacy on the public page."""
    if not full_name:
        return f"Candidate {cid}"
    parts = full_name.strip().split()
    if not parts:
        return f"Candidate {cid}"
    first = parts[0]
    last_initial = parts[-1][0] + "." if len(parts) > 1 else ""
    return f"{first} {last_initial}".strip()


def _public_shortlist(db: Session, role: Role, limit: int = 5) -> list[PublicShortlistCandidate]:
    sl = (
        db.query(Shortlist)
        .filter(Shortlist.role_id == role.id, Shortlist.agency_id == role.agency_id)
        .order_by(Shortlist.id.desc())
        .first()
    )
    if sl is None:
        return []
    entries = sl.entries[:limit]
    cand_ids = [e.candidate_id for e in entries]
    cand_map = {
        c.id: c
        for c in db.query(CandidateProfile).filter(CandidateProfile.id.in_(cand_ids)).all()
    }
    out: list[PublicShortlistCandidate] = []
    for e in entries:
        c = cand_map.get(e.candidate_id)
        skills = sorted({(s.name or "").strip() for s in (c.skills if c else []) if s.name})
        out.append(
            PublicShortlistCandidate(
                candidate_id=e.candidate_id,
                display_name=_client_display_name(c.full_name if c else None, e.candidate_id),
                headline=c.headline if c else None,
                years_experience=c.years_experience if c else None,
                fit_score=e.fit_score,
                top_skills=skills[:8],
            )
        )
    return out


@public_router.get("/roles/{token}", response_model=PublicRoleView)
def public_role_view(token: str, db: Session = Depends(get_db)):
    """Client-safe read-only role view. Bumps view_count for the recruiter's
    engagement tracking. Returns 404 if the token was revoked or never existed."""
    tok = (
        db.query(RoleShareToken)
        .filter(RoleShareToken.token == token, RoleShareToken.is_active.is_(True))
        .first()
    )
    if tok is None:
        raise HTTPException(status_code=404, detail="Share link is not active")
    role = db.get(Role, tok.role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role no longer exists")
    agency = db.get(Agency, role.agency_id)

    tok.view_count += 1
    tok.last_viewed_at = datetime.utcnow()
    db.commit()

    return PublicRoleView(
        role_id=role.id,
        title=role.title,
        seniority=role.seniority,
        location=role.location,
        employment_type=role.employment_type.value if role.employment_type else None,
        description=role.description,
        required_skills=role.required_skills or [],
        preferred_skills=role.preferred_skills or [],
        min_years_experience=role.min_years_experience,
        salary_min=role.salary_min,
        salary_max=role.salary_max,
        market_snapshot=role.market_snapshot,
        is_draft=role.is_draft,
        agency_name=agency.name if agency else "",
        shortlist=_public_shortlist(db, role),
    )


@public_router.post("/roles/{token}/feedback", response_model=RoleFeedbackOut, status_code=201)
def public_role_feedback(
    token: str,
    payload: PublicFeedbackCreate,
    db: Session = Depends(get_db),
):
    """Client submits sentiment/comment through the share link."""
    tok = (
        db.query(RoleShareToken)
        .filter(RoleShareToken.token == token, RoleShareToken.is_active.is_(True))
        .first()
    )
    if tok is None:
        raise HTTPException(status_code=404, detail="Share link is not active")

    # Basic guard: at least one of sentiment or body must be provided.
    if payload.sentiment is None and not (payload.body and payload.body.strip()):
        raise HTTPException(status_code=400, detail="Add a comment or a 👍/👎 to submit feedback")
    if payload.sentiment is not None and payload.sentiment not in (-1, 0, 1):
        raise HTTPException(status_code=400, detail="Sentiment must be -1, 0, or 1")

    # If the client identified a candidate, verify it's on the current shortlist
    # for this role — no drive-by feedback on random ids.
    if payload.candidate_id is not None:
        sl = (
            db.query(Shortlist)
            .filter(Shortlist.role_id == tok.role_id)
            .order_by(Shortlist.id.desc())
            .first()
        )
        if sl is None or payload.candidate_id not in {e.candidate_id for e in sl.entries}:
            raise HTTPException(status_code=400, detail="Candidate is not on the current shortlist")

    fb = RoleFeedback(
        agency_id=tok.agency_id,
        role_id=tok.role_id,
        share_token_id=tok.id,
        candidate_id=payload.candidate_id,
        sentiment=payload.sentiment,
        body=(payload.body or "").strip() or None,
        client_name=(payload.client_name or "").strip() or None,
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return fb


@roles_router.get("/{role_id}/feedback", response_model=list[RoleFeedbackOut])
def list_role_feedback(
    role_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found")
    return (
        db.query(RoleFeedback)
        .filter(RoleFeedback.role_id == role.id)
        .order_by(RoleFeedback.id.desc())
        .all()
    )


# ── Market analytics (self-contained over the agency's own data) ──────────
market_router = APIRouter(prefix="/agencies/{agency_id}/market", tags=["recruiter: market"])


@market_router.get("", response_model=MarketOverviewOut)
def market_overview(
    agency: Agency = Depends(require_agency_feature("market")),
    db: Session = Depends(get_db),
):
    """Demand vs supply, skill shortages, salary bands, and pipeline health."""
    return MarketOverviewOut(**asdict(compute_market(db, agency.id)))


@market_router.get("/snapshots", response_model=list[MarketSnapshotOut])
def list_market_snapshots(
    limit: int = Query(default=20, ge=1, le=100),
    agency: Agency = Depends(require_agency_feature("market")),
    db: Session = Depends(get_db),
):
    """Recent crawler snapshots across the agency's roles, newest first."""
    rows = (
        db.query(MarketSnapshot)
        .filter(MarketSnapshot.agency_id == agency.id)
        .order_by(MarketSnapshot.id.desc())
        .limit(limit)
        .all()
    )
    return rows


@market_router.post("/crawl", response_model=MarketCrawlResult, status_code=201)
def crawl_agency_market_endpoint(
    agency: Agency = Depends(require_agency_feature("market", write=True)),
    db: Session = Depends(get_db),
):
    """Fan-out crawl over the agency's most-common open-role titles."""
    snaps = crawl_agency_market(db, agency.id)
    return MarketCrawlResult(
        snapshots=[MarketSnapshotOut.model_validate(s) for s in snaps],
        total=len(snaps),
    )

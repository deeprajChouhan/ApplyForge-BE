"""Recruiter API routes (Phase 1). All agency-scoped routes enforce tenant
isolation through the get_agency dependency."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

import secrets

from app.recruiter.ids import PublicIdRoute
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
    AiInsightCache,
    Application,
    ApplicationNote,
    CandidateConsent,
    CandidateProfile,
    Client,
    ClientSlaConfig,
    ClientSubmission,
    Interview,
    InterviewFeedback,
    MarketSnapshot,
    Notification,
    Offer,
    OfferNegotiation,
    Placement,
    PostPlacementCheckin,
    Recruiter,
    RecruiterTask,
    Role,
    RoleFeedback,
    RoleShareToken,
    ClientShareToken,
    Shortlist,
    ShortlistEntry,
    SpecSheetTemplate,
    SubmissionFeedback,
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
    ClientShareTokenOut,
    PublicClientView,
    PublicClientRoleRow,
    PublicClientPlacementRow,
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
    PublicCandidateDetail,
    PublicCandidateExperience,
    PublicRoleView,
    PublicSubmission,
    PublicSubmissionDecision,
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
    ApplicationScreeningUpdate,
    ClientVisibleApplicationOut,
    MarketConfigOut,
    ScreeningExtractRequest,
    ScreeningExtractResult,
    ScreeningAutofillOut,
    ScreeningAutofillRequest,
    ScreeningImproveRequest,
    ScreeningImproveResult,
    ConsentCreate,
    ConsentUpdate,
    ConsentOut,
    SubmissionReadinessOut,
    ClientSubmissionCreate,
    ClientSubmissionUpdate,
    ClientSubmissionOut,
    AiSubmissionDraftRequest,
    AiSubmissionDraftResult,
    SubmissionFeedbackCreate,
    SubmissionFeedbackOut,
    ClientSlaConfigUpdate,
    ClientSlaConfigOut,
    CandidateComparisonRequest,
    CandidateComparisonOut,
    FeedbackSlaSummaryOut,
    InterviewCreate,
    InterviewUpdate,
    InterviewOut,
    InterviewFeedbackCreate,
    InterviewFeedbackOut,
    InterviewBriefOut,
    OfferCreate,
    OfferUpdate,
    OfferOut,
    OfferNegotiationCreate,
    OfferNegotiationOut,
    PlacementCreate,
    PlacementUpdate,
    PlacementOut,
    PostPlacementCheckinCreate,
    PostPlacementCheckinUpdate,
    PostPlacementCheckinOut,
    RecruiterTaskCreate,
    RecruiterTaskUpdate,
    RecruiterTaskOut,
    NotificationOut,
    NotificationBulkUpdate,
    DailyBriefOut,
    AskPoolRequest,
    AskPoolResult,
    RoleQualityCheckOut,
    RoleHealthOut,
    ClientIntelligenceOut,
)
from app.recruiter.bridge import provision_candidate
from app.recruiter.enums import UsageKind
from app.recruiter.services import usage as usage_service
from app.recruiter.services import market_config as market_config_service
from app.recruiter.services import visibility as visibility_service
from app.recruiter.services.screening import (
    apply_autofill,
    autofill_proposals,
    extract_structured_notes,
    improve_summary,
)
from app.recruiter.services.advisory import next_hire_advisory
from app.recruiter.services.client_analytics import compute_client_analytics
from app.recruiter.services.client_share_ai import build_summary as build_client_share_summary
from app.recruiter.services.ingestion import ingest_batch
from app.recruiter.services.linkedin_capture import capture_linkedin_profile
from app.recruiter.services.listing import generate_listing
from app.recruiter.services.market import compute_market
from app.recruiter.services.market_crawler import crawl_agency_market, crawl_role_market
from app.recruiter.services.matching import embed_role
from app.recruiter.services.placement import rank_roles_for_candidate
from app.recruiter.services.shortlist import generate_shortlist, refresh_role_fit_scores
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
clients_router = APIRouter(route_class=PublicIdRoute, prefix="/agencies/{agency_id}/clients", tags=["recruiter: clients"])


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


@clients_router.get("/{client_id}/share", response_model=ClientShareTokenOut | None)
def get_client_share(
    client_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    """Return the currently active client-share token, if any."""
    client = _load_client(db, agency, client_id)
    tok = (
        db.query(ClientShareToken)
        .filter(
            ClientShareToken.client_id == client.id,
            ClientShareToken.is_active.is_(True),
        )
        .order_by(ClientShareToken.id.desc())
        .first()
    )
    if not tok:
        return None
    return _client_share_out(tok)


@clients_router.post("/{client_id}/share", response_model=ClientShareTokenOut, status_code=201)
def create_client_share(
    client_id: int,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """Mint (or rotate) a public client-share token. Previous token is revoked.

    Also seeds the AI summary so the first client visit is fast; the summary
    can be re-generated on demand from POST .../share/ai-summary.
    """
    client = _load_client(db, agency, client_id)
    (
        db.query(ClientShareToken)
        .filter(
            ClientShareToken.client_id == client.id,
            ClientShareToken.is_active.is_(True),
        )
        .update({ClientShareToken.is_active: False})
    )
    tok = ClientShareToken(
        agency_id=agency.id,
        client_id=client.id,
        token=secrets.token_urlsafe(24),
        is_active=True,
    )
    db.add(tok)
    db.flush()
    try:
        summary, used_llm = build_client_share_summary(db, client)
        tok.ai_summary = summary
        tok.ai_summary_generated_at = datetime.utcnow()
        tok.ai_summary_used_llm = used_llm
    except Exception:
        # AI seeding is best-effort; a failure never blocks the share link.
        tok.ai_summary = None
    db.commit()
    db.refresh(tok)
    return _client_share_out(tok)


@clients_router.delete("/{client_id}/share", status_code=204)
def revoke_client_share(
    client_id: int,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """Revoke the active client-share token. Old URL immediately 404s."""
    client = _load_client(db, agency, client_id)
    (
        db.query(ClientShareToken)
        .filter(
            ClientShareToken.client_id == client.id,
            ClientShareToken.is_active.is_(True),
        )
        .update({ClientShareToken.is_active: False})
    )
    db.commit()
    return None


@clients_router.post(
    "/{client_id}/share/ai-summary",
    response_model=ClientShareTokenOut,
)
def regenerate_client_share_summary(
    client_id: int,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """Rebuild the AI summary on the active share token. Grounded in analytics."""
    client = _load_client(db, agency, client_id)
    tok = (
        db.query(ClientShareToken)
        .filter(
            ClientShareToken.client_id == client.id,
            ClientShareToken.is_active.is_(True),
        )
        .order_by(ClientShareToken.id.desc())
        .first()
    )
    if tok is None:
        raise HTTPException(status_code=404, detail="No active share link for this client")
    summary, used_llm = build_client_share_summary(db, client)
    tok.ai_summary = summary
    tok.ai_summary_generated_at = datetime.utcnow()
    tok.ai_summary_used_llm = used_llm
    db.commit()
    db.refresh(tok)
    return _client_share_out(tok)


def _client_share_out(tok: ClientShareToken) -> ClientShareTokenOut:
    return ClientShareTokenOut(
        id=tok.id,
        client_id=tok.client_id,
        token=tok.token,
        is_active=tok.is_active,
        view_count=tok.view_count,
        last_viewed_at=tok.last_viewed_at,
        ai_summary=tok.ai_summary,
        ai_summary_generated_at=tok.ai_summary_generated_at,
        ai_summary_used_llm=tok.ai_summary_used_llm,
        created_at=tok.created_at,
        share_url=None,
    )


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
roles_router = APIRouter(route_class=PublicIdRoute, prefix="/agencies/{agency_id}/roles", tags=["recruiter: roles"])


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


@roles_router.delete("/{role_id}/market", status_code=204)
def clear_market_snapshot(
    role_id: int,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    """Remove the cached market snapshot for this role. Idempotent —
    returns 204 whether or not a snapshot was set."""
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.market_snapshot is not None:
        role.market_snapshot = None
        db.commit()
    return None


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
    route_class=PublicIdRoute,
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
    route_class=PublicIdRoute,
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
    route_class=PublicIdRoute,
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
    route_class=PublicIdRoute,
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
    # Fit scores on cards are a cache of the latest ranking — reconcile on read
    # so the board always agrees with the ranked shortlist.
    if refresh_role_fit_scores(db, role):
        db.commit()
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
                    key=lambda r: (
                        r.fit_score if r.fit_score is not None else -1.0,
                        r.last_activity_at or datetime.min,
                    ),
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
public_router = APIRouter(route_class=PublicIdRoute, prefix="/public", tags=["recruiter: public"])


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


@public_router.get("/clients/{token}", response_model=PublicClientView)
def public_client_view(token: str, db: Session = Depends(get_db)):
    """Client-facing status page. Bumps view_count; excludes candidate PII.

    Guardrails:
     - No candidate names/emails/phones, no compensation numbers.
     - Roles reduced to title, seniority, status and stage counts.
     - Placements reduced to role title + start date only.
     - AI summary is cached on the token; the client sees whatever the recruiter
       last regenerated.
    """
    tok = (
        db.query(ClientShareToken)
        .filter(
            ClientShareToken.token == token,
            ClientShareToken.is_active.is_(True),
        )
        .first()
    )
    if tok is None:
        raise HTTPException(status_code=404, detail="Share link is not active")
    client = db.get(Client, tok.client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client no longer exists")
    agency = db.get(Agency, client.agency_id)

    tok.view_count += 1
    tok.last_viewed_at = datetime.utcnow()
    db.commit()

    analytics = compute_client_analytics(db, client)

    # Fold Application stage counts into per-role rows. compute_client_analytics
    # already returns per-role active_pipeline + placed; we add finer submitted/
    # interviewing/offer counts derived from the same apps table.
    from app.recruiter.enums import ApplicationStage
    role_ids = [r["id"] for r in analytics.get("roles", []) if r.get("id")]
    stage_by_role: dict[int, dict[str, int]] = {}
    if role_ids:
        apps = (
            db.query(Application)
            .filter(
                Application.agency_id == client.agency_id,
                Application.role_id.in_(role_ids),
            )
            .all()
        )
        for a in apps:
            if a.role_id is None:
                continue
            counts = stage_by_role.setdefault(
                a.role_id,
                {"submitted": 0, "interviewing": 0, "offer": 0, "placed": 0, "last": None},
            )
            if a.stage in (ApplicationStage.submitted, ApplicationStage.screening):
                counts["submitted"] += 1
            elif a.stage == ApplicationStage.interview:
                counts["interviewing"] += 1
            elif a.stage == ApplicationStage.offer:
                counts["offer"] += 1
            elif a.stage == ApplicationStage.placed:
                counts["placed"] += 1
            if a.last_activity_at and (counts["last"] is None or a.last_activity_at > counts["last"]):
                counts["last"] = a.last_activity_at

    role_rows: list[PublicClientRoleRow] = []
    for r in analytics.get("roles", []):
        rid = r.get("id")
        c = stage_by_role.get(rid, {})
        role_rows.append(
            PublicClientRoleRow(
                role_id=rid,
                title=r.get("title") or "",
                seniority=r.get("seniority"),
                status=r.get("status") or "",
                is_draft=bool(r.get("is_draft")),
                active_pipeline=r.get("active_pipeline") or 0,
                submitted=int(c.get("submitted") or 0),
                interviewing=int(c.get("interviewing") or 0),
                offer=int(c.get("offer") or 0),
                placed=int(c.get("placed") or r.get("placed") or 0),
                last_activity_at=c.get("last"),
            )
        )

    recent_rows = [
        PublicClientPlacementRow(
            role_title=p.get("role_title"),
            start_date=p.get("placed_at").date() if p.get("placed_at") else None,
        )
        for p in (analytics.get("recent_placements") or [])[:5]
    ]

    return PublicClientView(
        agency_name=agency.name if agency else "",
        client_name=client.name,
        industry=client.industry,
        generated_at=datetime.utcnow(),
        roles_open=analytics.get("roles_open") or 0,
        roles_filled=analytics.get("roles_filled") or 0,
        active_pipeline=analytics.get("active_pipeline") or 0,
        placements_total=analytics.get("placements_total") or 0,
        avg_time_to_fill_days=analytics.get("avg_time_to_fill_days"),
        top_skills=list((analytics.get("top_skills") or [])[:10]),
        roles=role_rows,
        recent_placements=recent_rows,
        ai_summary=tok.ai_summary,
        ai_summary_generated_at=tok.ai_summary_generated_at,
        ai_summary_used_llm=tok.ai_summary_used_llm,
    )


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
    # First-view stamp on each not-yet-viewed submission for this role.
    now = datetime.utcnow()
    for _sub in db.query(ClientSubmission).filter(
        ClientSubmission.agency_id == role.agency_id,
        ClientSubmission.role_id == role.id,
        ClientSubmission.client_viewed_at.is_(None),
        ClientSubmission.status.in_(["submitted", "client_reviewing"]),
    ).all():
        _sub.client_viewed_at = now
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
        submissions=_public_submissions(db, role),
        placement_journey=_placement_journey_payload(db, role),
    )


@public_router.get(
    "/roles/{token}/candidates/{candidate_id}",
    response_model=PublicCandidateDetail,
)
def public_role_candidate(
    token: str,
    candidate_id: int,
    db: Session = Depends(get_db),
):
    """Client-safe full profile for a candidate on the shared shortlist.

    Guards: the token must be active and the candidate must appear on the
    most recent shortlist for the role. Deliberately excludes email, phone,
    expected budget, source file, and provisioning ids — the public share is
    boardroom-safe.
    """
    tok = (
        db.query(RoleShareToken)
        .filter(RoleShareToken.token == token, RoleShareToken.is_active.is_(True))
        .first()
    )
    if tok is None:
        raise HTTPException(status_code=404, detail="Share link is not active")

    sl = (
        db.query(Shortlist)
        .filter(Shortlist.role_id == tok.role_id, Shortlist.agency_id == tok.agency_id)
        .order_by(Shortlist.id.desc())
        .first()
    )
    if sl is None:
        raise HTTPException(status_code=404, detail="Candidate is not on the shortlist")
    entry = next((e for e in sl.entries if e.candidate_id == candidate_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Candidate is not on the shortlist")

    cand = db.get(CandidateProfile, candidate_id)
    if cand is None or cand.agency_id != tok.agency_id:
        raise HTTPException(status_code=404, detail="Candidate no longer exists")

    skills = sorted({(s.name or "").strip() for s in cand.skills if s.name})
    experiences = sorted(
        cand.experiences,
        key=lambda e: (e.start_date or date.min),
        reverse=True,
    )
    return PublicCandidateDetail(
        candidate_id=cand.id,
        display_name=_client_display_name(cand.full_name, cand.id),
        headline=cand.headline,
        location=cand.location,
        years_experience=cand.years_experience,
        fit_score=entry.fit_score,
        summary=cand.summary,
        skills=skills,
        experiences=[
            PublicCandidateExperience(
                title=e.title,
                company=e.company,
                start_date=e.start_date,
                end_date=e.end_date,
                description=e.description,
            )
            for e in experiences
        ],
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
market_router = APIRouter(route_class=PublicIdRoute, prefix="/agencies/{agency_id}/market", tags=["recruiter: market"])


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



# ── Recruiter OS Phase 1: role-specific screening ───────────────────────

def _load_application_or_404(db, agency, application_id: int) -> Application:
    app_row = db.get(Application, application_id)
    if app_row is None or app_row.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Application not found")
    return app_row


@applications_router.get("/{application_id}/screening", response_model=ApplicationOut)
def get_application_screening(
    application_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    app_row = _load_application_or_404(db, agency, application_id)
    role = db.get(Role, app_row.role_id) if app_row.role_id else None
    if role is not None and role.agency_id == agency.id:
        if refresh_role_fit_scores(db, role):
            db.commit()
            db.refresh(app_row)
    return app_row


@applications_router.patch("/{application_id}/screening", response_model=ApplicationOut)
def update_application_screening(
    application_id: int,
    payload: ApplicationScreeningUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    recruiter: Recruiter | None = Depends(_soft_recruiter),
    db: Session = Depends(get_db),
):
    """
    Partial update for the role-specific screening block. Fields omitted from
    the payload are left unchanged. Screening only makes sense when the
    application is linked to a role — otherwise 400.
    """
    app_row = _load_application_or_404(db, agency, application_id)
    if app_row.role_id is None:
        raise HTTPException(
            status_code=400,
            detail="Screening requires the application to be linked to a role.",
        )

    data = payload.model_dump(exclude_unset=True)
    data.pop("reopen", None)
    prev_outcome = app_row.screening_outcome
    actor_id = recruiter.id if recruiter else None
    actor_name = (recruiter.full_name or recruiter.email) if recruiter else None

    was_locked = app_row.screening_completed_at is not None
    amended_fields: list[str] = []
    if was_locked and payload.reopen:
        app_row.screening_completed_at = None
        app_row.screening_completed_by = None
        was_locked = False
        db.add(
            ApplicationNote(
                agency_id=agency.id,
                application_id=app_row.id,
                author_recruiter_id=actor_id,
                author_name=actor_name,
                kind="system",
                body="Screening reopened for editing",
            )
        )
    elif was_locked:
        # Locked screening: only fill gaps. Anything that already holds a value
        # needs an explicit reopen so the completed record isn't silently
        # rewritten after the fact.
        always_editable = {"assigned_recruiter_id", "client_visibility", "mark_completed"}
        blocked: list[str] = []
        for field in data:
            if field in always_editable:
                continue
            if field == "availability_immediate":
                current_set = app_row.availability_immediate or app_row.availability_date is not None
            elif field == "availability_date":
                current_set = app_row.availability_immediate or app_row.availability_date is not None
            elif field == "expected_compensation":
                current_set = submissions_service.compensation_confirmed(app_row.expected_compensation)
            else:
                cur = getattr(app_row, field, None)
                current_set = cur not in (None, "", [], {})
            if current_set:
                # Idempotent re-sends of the same value are fine.
                if field in ("expected_compensation", "current_compensation", "notice_period"):
                    continue_ok = False
                else:
                    continue_ok = data[field] == getattr(app_row, field, None)
                if not continue_ok:
                    blocked.append(field)
            elif data[field] not in (None, "", [], {}, False):
                amended_fields.append(field)
        if blocked:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Screening is locked. Reopen it to change: "
                    + ", ".join(sorted(blocked))
                ),
            )

    if payload.expected_compensation is not None:
        app_row.expected_compensation = payload.expected_compensation.model_dump(exclude_none=True)
        data.pop("expected_compensation", None)
    if payload.current_compensation is not None:
        app_row.current_compensation = payload.current_compensation.model_dump(exclude_none=True)
        data.pop("current_compensation", None)
    if payload.notice_period is not None:
        np = payload.notice_period.model_dump(exclude_none=True)
        if np.get("available_from") and hasattr(np["available_from"], "isoformat"):
            np["available_from"] = np["available_from"].isoformat()
        app_row.notice_period = np
        data.pop("notice_period", None)

    if "screening_outcome" in data and data["screening_outcome"] is not None:
        from app.recruiter.enums import ScreeningOutcome
        try:
            app_row.screening_outcome = ScreeningOutcome(data["screening_outcome"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid screening_outcome")
        data.pop("screening_outcome")
    if "preferred_work_model" in data and data["preferred_work_model"] is not None:
        from app.recruiter.enums import WorkModel
        try:
            app_row.preferred_work_model = WorkModel(data["preferred_work_model"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid preferred_work_model")
        data.pop("preferred_work_model")
    if "relocation" in data and data["relocation"] is not None:
        from app.recruiter.enums import RelocationPreference
        try:
            app_row.relocation = RelocationPreference(data["relocation"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid relocation preference")
        data.pop("relocation")

    for field in (
        "recruiter_summary", "candidate_motivation", "internal_notes",
        "motivation_categories", "availability_date", "availability_immediate",
        "preferred_location", "relocation_notes", "assigned_recruiter_id",
        "client_visibility",
    ):
        if field in data:
            setattr(app_row, field, data[field])

    # Smart fill: whenever the recruiter's narrative changes, lift any facts it
    # states (salary, notice, LWD, work model, relocation…) into the empty
    # structured fields. Never overwrites a value the recruiter entered.
    auto_filled: list[str] = []
    if any(data.get(k) for k in ("recruiter_summary", "internal_notes")):
        role_for_fill = db.get(Role, app_row.role_id) if app_row.role_id else None
        _, proposals = autofill_proposals(app_row, role_for_fill)
        explicitly_sent = {k for k, v in data.items() if v not in (None, "", [], {})}
        if payload.expected_compensation is not None and payload.expected_compensation.amount:
            explicitly_sent.add("expected_compensation")
        if payload.notice_period is not None:
            explicitly_sent.add("notice_period")
        auto_filled = apply_autofill(
            app_row, [p for p in proposals if p["field"] not in explicitly_sent]
        )

    if payload.mark_completed and app_row.screening_completed_at is None:
        app_row.screening_completed_at = datetime.utcnow()
        app_row.screening_completed_by = recruiter.id if recruiter else None

    if auto_filled:
        db.add(
            ApplicationNote(
                agency_id=agency.id,
                application_id=app_row.id,
                author_recruiter_id=actor_id,
                author_name=actor_name,
                kind="system",
                body="Auto-filled from summary: "
                + ", ".join(f.replace("_", " ") for f in auto_filled),
            )
        )
    if amended_fields:
        db.add(
            ApplicationNote(
                agency_id=agency.id,
                application_id=app_row.id,
                author_recruiter_id=actor_id,
                author_name=actor_name,
                kind="system",
                body="Locked screening amended — filled: "
                + ", ".join(f.replace("_", " ") for f in amended_fields),
            )
        )

    app_row.last_activity_at = datetime.utcnow()

    if app_row.screening_outcome != prev_outcome and app_row.screening_outcome is not None:
        db.add(
            ApplicationNote(
                agency_id=agency.id,
                application_id=app_row.id,
                author_recruiter_id=recruiter.id if recruiter else None,
                author_name=(recruiter.full_name or recruiter.email) if recruiter else None,
                kind="system",
                body=f"Screening outcome set to {app_row.screening_outcome.value}",
            )
        )

    db.commit()
    db.refresh(app_row)
    return app_row


@applications_router.get(
    "/{application_id}/client-view",
    response_model=ClientVisibleApplicationOut,
)
def application_client_view(
    application_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    """
    Server-side client-visible serialisation. Recruiter-facing (used to
    preview what the client will see); Phase 2 client portal endpoints will
    also route through visibility_service.to_client_view.
    """
    app_row = _load_application_or_404(db, agency, application_id)
    return visibility_service.to_client_view(app_row)


@applications_router.post(
    "/{application_id}/screening/ai-improve-summary",
    response_model=ScreeningImproveResult,
)
def ai_improve_summary(
    application_id: int,
    payload: ScreeningImproveRequest,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    app_row = _load_application_or_404(db, agency, application_id)
    cand = db.get(CandidateProfile, app_row.candidate_id)
    role = db.get(Role, app_row.role_id) if app_row.role_id else None
    return improve_summary(
        payload.rough_notes,
        cand_name=(cand.full_name if cand else None),
        role_title=(role.title if role else None),
    )


@applications_router.post(
    "/{application_id}/screening/ai-extract-notes",
    response_model=ScreeningExtractResult,
)
def ai_extract_notes(
    application_id: int,
    payload: ScreeningExtractRequest,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    app_row = _load_application_or_404(db, agency, application_id)
    role = db.get(Role, app_row.role_id) if app_row.role_id else None
    return extract_structured_notes(
        payload.rough_notes,
        country_code=(role.country_code if role else None),
    )


@applications_router.post(
    "/{application_id}/screening/autofill",
    response_model=ScreeningAutofillOut,
)
def screening_autofill(
    application_id: int,
    payload: ScreeningAutofillRequest,
    agency: Agency = Depends(require_unlocked_agency),
    recruiter: Recruiter | None = Depends(_soft_recruiter),
    db: Session = Depends(get_db),
):
    """
    Read the saved summary / notes and propose structured screening values.
    Works on locked screenings too: `fill` proposals only ever target empty
    fields; `conflict` proposals are written only when listed in `overwrite`
    (an explicit recruiter decision, logged to the activity trail).
    """
    app_row = _load_application_or_404(db, agency, application_id)
    role = db.get(Role, app_row.role_id) if app_row.role_id else None
    extracted, proposals = autofill_proposals(app_row, role)
    written: list[str] = []
    if payload.apply:
        only = set(payload.fields) if payload.fields else None
        overwrite = set(payload.overwrite or [])
        before = {p["field"]: p["current"] for p in proposals}
        written = apply_autofill(app_row, proposals, only=only, overwrite=overwrite)
        if written:
            app_row.last_activity_at = datetime.utcnow()
            overwritten = [f for f in written if f in overwrite]
            filled = [f for f in written if f not in overwrite]
            lines = []
            if filled:
                lines.append("filled " + ", ".join(f.replace("_", " ") for f in filled))
            if overwritten:
                lines.append(
                    "replaced "
                    + ", ".join(f"{f.replace('_', ' ')} (was {before.get(f)!s})" for f in overwritten)
                )
            db.add(
                ApplicationNote(
                    agency_id=agency.id,
                    application_id=app_row.id,
                    author_recruiter_id=recruiter.id if recruiter else None,
                    author_name=(recruiter.full_name or recruiter.email) if recruiter else None,
                    kind="system",
                    body="Auto-fill from summary — " + "; ".join(lines),
                )
            )
            db.commit()
            db.refresh(app_row)
            _, proposals = autofill_proposals(app_row, role, use_llm=False)
    return ScreeningAutofillOut(
        proposals=proposals,
        written=written,
        last_working_day=extracted.get("last_working_day"),
        early_release_date=extracted.get("early_release_date"),
        early_release_confirmed=extracted.get("early_release_confirmed"),
        application=ApplicationOut.model_validate(app_row),
    )


# ── Market configuration (static reference data) ────────────────────────
market_config_router = APIRouter(route_class=PublicIdRoute, prefix="/market-configs", tags=["recruiter: market-config"])


@market_config_router.get("", response_model=list[MarketConfigOut])
def list_market_configs():
    return [MarketConfigOut(**c.to_dict()) for c in market_config_service.all_configs()]


@market_config_router.get("/{country_code}", response_model=MarketConfigOut)
def get_market_config(country_code: str):
    if not market_config_service.is_supported(country_code):
        raise HTTPException(status_code=404, detail="Unsupported country")
    return MarketConfigOut(**market_config_service.get_config(country_code).to_dict())


# ═══════════════════════════════════════════════════════════════════════
# Recruiter OS — Phase 2 routes: Consent + Submission workflow
# ═══════════════════════════════════════════════════════════════════════
from app.recruiter.services import submissions as submissions_service
from app.recruiter.services import collaboration as collaboration_service
from app.recruiter.services import interviews as interviews_service
from app.recruiter.services import offers as offers_service
from app.recruiter.services import productivity as productivity_service
from app.recruiter.services import intelligence as intelligence_service


consent_router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/agencies/{agency_id}/consents", tags=["recruiter: consent"]
)


@consent_router.post("", response_model=ConsentOut, status_code=201)
def create_consent(
    payload: ConsentCreate,
    agency: Agency = Depends(require_unlocked_agency),
    recruiter: Recruiter | None = Depends(_soft_recruiter),
    db: Session = Depends(get_db),
):
    cand = db.get(CandidateProfile, payload.candidate_id)
    if cand is None or cand.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Candidate not found")
    role = db.get(Role, payload.role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found")
    from app.recruiter.enums import ConsentStatus, ConsentMethod
    try:
        status = ConsentStatus(payload.status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid consent status")
    method = None
    if payload.method:
        try:
            method = ConsentMethod(payload.method)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid consent method")
    consent = CandidateConsent(
        agency_id=agency.id, candidate_id=payload.candidate_id,
        role_id=payload.role_id, status=status, method=method,
        captured_at=datetime.utcnow() if status == ConsentStatus.confirmed else None,
        captured_by=recruiter.id if recruiter else None,
        evidence=payload.evidence, expires_at=payload.expires_at,
    )
    db.add(consent); db.commit(); db.refresh(consent)
    return consent


@consent_router.get("", response_model=list[ConsentOut])
def list_consents(
    agency: Agency = Depends(get_agency),
    candidate_id: int | None = Query(default=None),
    role_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(CandidateConsent).filter(CandidateConsent.agency_id == agency.id)
    if candidate_id is not None:
        q = q.filter(CandidateConsent.candidate_id == candidate_id)
    if role_id is not None:
        q = q.filter(CandidateConsent.role_id == role_id)
    return q.order_by(CandidateConsent.id.desc()).all()


@consent_router.patch("/{consent_id}", response_model=ConsentOut)
def update_consent(
    consent_id: int,
    payload: ConsentUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    recruiter: Recruiter | None = Depends(_soft_recruiter),
    db: Session = Depends(get_db),
):
    consent = db.get(CandidateConsent, consent_id)
    if consent is None or consent.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Consent not found")
    from app.recruiter.enums import ConsentStatus, ConsentMethod
    if payload.status:
        try:
            consent.status = ConsentStatus(payload.status)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid consent status")
        if consent.status == ConsentStatus.confirmed and consent.captured_at is None:
            consent.captured_at = datetime.utcnow()
            consent.captured_by = recruiter.id if recruiter else None
    if payload.method:
        try:
            consent.method = ConsentMethod(payload.method)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid consent method")
    if payload.evidence is not None:
        consent.evidence = payload.evidence
    if payload.expires_at is not None:
        consent.expires_at = payload.expires_at
    db.commit(); db.refresh(consent)
    return consent


# ── Submission readiness + ClientSubmission CRUD ────────────────────
submissions_router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/agencies/{agency_id}/submissions", tags=["recruiter: submissions"]
)


@applications_router.get(
    "/{application_id}/readiness", response_model=SubmissionReadinessOut,
)
def application_readiness(
    application_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    app_row = _load_application_or_404(db, agency, application_id)
    return submissions_service.readiness(db, agency.id, app_row)


@submissions_router.post("", response_model=ClientSubmissionOut, status_code=201)
def create_submission(
    payload: ClientSubmissionCreate,
    agency: Agency = Depends(require_unlocked_agency),
    recruiter: Recruiter | None = Depends(_soft_recruiter),
    db: Session = Depends(get_db),
):
    app_row = _load_application_or_404(db, agency, payload.application_id)
    if app_row.role_id is None:
        raise HTTPException(status_code=400, detail="Application must be linked to a role")
    role = db.get(Role, app_row.role_id)
    sub = ClientSubmission(
        agency_id=agency.id,
        client_id=role.client_id if role else None,
        role_id=app_row.role_id,
        candidate_id=app_row.candidate_id,
        application_id=app_row.id,
        client_summary=payload.client_summary,
        key_strengths=payload.key_strengths,
        potential_gaps=payload.potential_gaps,
        cv_version_id=payload.cv_version_id,
    )
    submissions_service.snapshot_screening_into(sub, app_row)
    if payload.mark_submitted:
        from app.recruiter.enums import SubmissionStatus
        sub.status = SubmissionStatus.submitted
        sub.submitted_at = datetime.utcnow()
        sub.submitted_by = recruiter.id if recruiter else None
    db.add(sub); db.commit(); db.refresh(sub)
    return sub


@submissions_router.get("", response_model=list[ClientSubmissionOut])
def list_submissions(
    agency: Agency = Depends(get_agency),
    role_id: int | None = Query(default=None),
    candidate_id: int | None = Query(default=None),
    client_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(ClientSubmission).filter(ClientSubmission.agency_id == agency.id)
    if role_id is not None: q = q.filter(ClientSubmission.role_id == role_id)
    if candidate_id is not None: q = q.filter(ClientSubmission.candidate_id == candidate_id)
    if client_id is not None: q = q.filter(ClientSubmission.client_id == client_id)
    return q.order_by(ClientSubmission.id.desc()).all()


@submissions_router.get("/{submission_id}", response_model=ClientSubmissionOut)
def get_submission(
    submission_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    sub = db.get(ClientSubmission, submission_id)
    if sub is None or sub.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Submission not found")
    return sub


@submissions_router.patch("/{submission_id}", response_model=ClientSubmissionOut)
def update_submission(
    submission_id: int,
    payload: ClientSubmissionUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    recruiter: Recruiter | None = Depends(_soft_recruiter),
    db: Session = Depends(get_db),
):
    sub = db.get(ClientSubmission, submission_id)
    if sub is None or sub.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Submission not found")
    from app.recruiter.enums import SubmissionStatus, ClientDecision
    if payload.client_summary is not None: sub.client_summary = payload.client_summary
    if payload.key_strengths is not None: sub.key_strengths = payload.key_strengths
    if payload.potential_gaps is not None: sub.potential_gaps = payload.potential_gaps
    if payload.cv_version_id is not None: sub.cv_version_id = payload.cv_version_id
    if payload.status is not None:
        try:
            new_status = SubmissionStatus(payload.status)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid submission status")
        if new_status == SubmissionStatus.submitted and sub.status != SubmissionStatus.submitted:
            sub.submitted_at = datetime.utcnow()
            sub.submitted_by = recruiter.id if recruiter else None
        sub.status = new_status
    if payload.client_decision:
        try:
            sub.client_decision = ClientDecision(payload.client_decision)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid client decision")
    if payload.client_feedback is not None: sub.client_feedback = payload.client_feedback
    if payload.client_viewed_at is not None: sub.client_viewed_at = payload.client_viewed_at
    if payload.client_responded_at is not None: sub.client_responded_at = payload.client_responded_at
    db.commit(); db.refresh(sub)
    return sub


@submissions_router.post(
    "/{submission_id}/ai-draft", response_model=AiSubmissionDraftResult,
)
def ai_draft_submission(
    submission_id: int,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    sub = db.get(ClientSubmission, submission_id)
    if sub is None or sub.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Submission not found")
    app_row = db.get(Application, sub.application_id)
    cand = db.get(CandidateProfile, sub.candidate_id)
    role = db.get(Role, sub.role_id)
    return submissions_service.ai_draft_submission(app_row, cand, role)


# ═══════════════════════════════════════════════════════════════════════
# Recruiter OS — Phase 3 routes: feedback, comparison, SLA
# ═══════════════════════════════════════════════════════════════════════

@submissions_router.post(
    "/{submission_id}/feedback", response_model=SubmissionFeedbackOut, status_code=201,
)
def create_submission_feedback(
    submission_id: int,
    payload: SubmissionFeedbackCreate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    sub = db.get(ClientSubmission, submission_id)
    if sub is None or sub.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Submission not found")
    from app.recruiter.enums import ClientDecision
    try:
        decision = ClientDecision(payload.decision)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid decision")
    fb = SubmissionFeedback(
        agency_id=agency.id, submission_id=submission_id, decision=decision,
        reasons=payload.reasons, comment=payload.comment,
        client_contact_name=payload.client_contact_name,
        client_contact_email=payload.client_contact_email,
        submitted_via=payload.submitted_via,
    )
    db.add(fb)
    # Mirror decision + first responded timestamp onto the submission
    sub.client_decision = decision
    if sub.client_responded_at is None:
        sub.client_responded_at = datetime.utcnow()
    db.commit(); db.refresh(fb)
    return fb


@submissions_router.get(
    "/{submission_id}/feedback", response_model=list[SubmissionFeedbackOut],
)
def list_submission_feedback(
    submission_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    sub = db.get(ClientSubmission, submission_id)
    if sub is None or sub.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Submission not found")
    return (
        db.query(SubmissionFeedback)
        .filter(SubmissionFeedback.submission_id == submission_id)
        .order_by(SubmissionFeedback.id.desc()).all()
    )


sla_router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/agencies/{agency_id}/clients/{client_id}", tags=["recruiter: sla"]
)


@sla_router.get("/sla", response_model=ClientSlaConfigOut | None)
def get_client_sla(
    client_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    cfg = db.query(ClientSlaConfig).filter(
        ClientSlaConfig.agency_id == agency.id, ClientSlaConfig.client_id == client_id,
    ).first()
    return cfg


@sla_router.put("/sla", response_model=ClientSlaConfigOut)
def upsert_client_sla(
    client_id: int,
    payload: ClientSlaConfigUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Client not found")
    cfg = db.query(ClientSlaConfig).filter(
        ClientSlaConfig.agency_id == agency.id, ClientSlaConfig.client_id == client_id,
    ).first()
    if cfg is None:
        cfg = ClientSlaConfig(agency_id=agency.id, client_id=client_id)
        db.add(cfg)
    cfg.expected_feedback_hours = payload.expected_feedback_hours
    cfg.notify_recruiter = payload.notify_recruiter
    db.commit(); db.refresh(cfg)
    return cfg


@sla_router.get("/sla-summary", response_model=FeedbackSlaSummaryOut)
def client_sla_summary(
    client_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Client not found")
    return collaboration_service.sla_summary(db, agency.id, client_id)


comparison_router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/agencies/{agency_id}/candidate-comparison",
    tags=["recruiter: comparison"],
)


@comparison_router.post("", response_model=CandidateComparisonOut)
def compare_candidates(
    payload: CandidateComparisonRequest,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    role = db.get(Role, payload.role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found")
    return collaboration_service.candidate_comparison(
        db, agency.id, payload.role_id, payload.candidate_ids,
    )


# ═══════════════════════════════════════════════════════════════════════
# Recruiter OS — Phase 4 routes: Interviews
# ═══════════════════════════════════════════════════════════════════════
interviews_router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/agencies/{agency_id}/interviews", tags=["recruiter: interviews"]
)


def _load_interview_or_404(db, agency, iid: int) -> Interview:
    iv = db.get(Interview, iid)
    if iv is None or iv.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Interview not found")
    return iv


@interviews_router.post("", response_model=InterviewOut, status_code=201)
def create_interview(
    payload: InterviewCreate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    app_row = _load_application_or_404(db, agency, payload.application_id)
    from app.recruiter.enums import InterviewStage, InterviewType
    try:
        stage = InterviewStage(payload.stage)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid interview stage")
    itype = None
    if payload.interview_type:
        try: itype = InterviewType(payload.interview_type)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid interview_type")
    role = db.get(Role, app_row.role_id) if app_row.role_id else None
    iv = Interview(
        agency_id=agency.id, application_id=app_row.id, role_id=app_row.role_id,
        candidate_id=app_row.candidate_id,
        client_id=role.client_id if role else None,
        stage=stage, interview_type=itype,
        interviewers=payload.interviewers,
        proposed_times=[t.isoformat() for t in (payload.proposed_times or [])],
        confirmed_time=payload.confirmed_time,
        duration_minutes=payload.duration_minutes,
        location=payload.location, meeting_url=payload.meeting_url,
        notes=payload.notes,
    )
    db.add(iv); db.commit(); db.refresh(iv)
    return iv


@interviews_router.get("", response_model=list[InterviewOut])
def list_interviews(
    agency: Agency = Depends(get_agency),
    application_id: int | None = Query(default=None),
    role_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(Interview).filter(Interview.agency_id == agency.id)
    if application_id is not None: q = q.filter(Interview.application_id == application_id)
    if role_id is not None: q = q.filter(Interview.role_id == role_id)
    return q.order_by(Interview.id.desc()).all()


@interviews_router.get("/{interview_id}", response_model=InterviewOut)
def get_interview(
    interview_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    return _load_interview_or_404(db, agency, interview_id)


@interviews_router.patch("/{interview_id}", response_model=InterviewOut)
def update_interview(
    interview_id: int,
    payload: InterviewUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    iv = _load_interview_or_404(db, agency, interview_id)
    from app.recruiter.enums import InterviewStage, InterviewType, InterviewStatus
    if payload.stage:
        try: iv.stage = InterviewStage(payload.stage)
        except ValueError: raise HTTPException(400, "Invalid interview stage")
    if payload.interview_type is not None:
        try: iv.interview_type = InterviewType(payload.interview_type) if payload.interview_type else None
        except ValueError: raise HTTPException(400, "Invalid interview_type")
    if payload.status:
        try: iv.status = InterviewStatus(payload.status)
        except ValueError: raise HTTPException(400, "Invalid interview status")
    if payload.interviewers is not None: iv.interviewers = payload.interviewers
    if payload.proposed_times is not None:
        iv.proposed_times = [t.isoformat() for t in payload.proposed_times]
    if payload.confirmed_time is not None: iv.confirmed_time = payload.confirmed_time
    if payload.duration_minutes is not None: iv.duration_minutes = payload.duration_minutes
    if payload.location is not None: iv.location = payload.location
    if payload.meeting_url is not None: iv.meeting_url = payload.meeting_url
    if payload.notes is not None: iv.notes = payload.notes
    db.commit(); db.refresh(iv)
    return iv


@interviews_router.post(
    "/{interview_id}/feedback", response_model=InterviewFeedbackOut, status_code=201,
)
def create_interview_feedback(
    interview_id: int,
    payload: InterviewFeedbackCreate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    iv = _load_interview_or_404(db, agency, interview_id)
    from app.recruiter.enums import ClientDecision
    dec = None
    if payload.decision:
        try: dec = ClientDecision(payload.decision)
        except ValueError: raise HTTPException(400, "Invalid decision")
    fb = InterviewFeedback(
        agency_id=agency.id, interview_id=interview_id,
        interviewer_name=payload.interviewer_name,
        technical=payload.technical, communication=payload.communication,
        role_understanding=payload.role_understanding,
        domain_knowledge=payload.domain_knowledge,
        leadership=payload.leadership, culture_alignment=payload.culture_alignment,
        decision=dec, comment=payload.comment,
    )
    db.add(fb); db.commit(); db.refresh(fb)
    return fb


@interviews_router.get(
    "/{interview_id}/feedback", response_model=list[InterviewFeedbackOut],
)
def list_interview_feedback(
    interview_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    _load_interview_or_404(db, agency, interview_id)
    return (
        db.query(InterviewFeedback)
        .filter(InterviewFeedback.interview_id == interview_id)
        .order_by(InterviewFeedback.id.desc()).all()
    )


@interviews_router.get("/{interview_id}/brief", response_model=InterviewBriefOut)
def interview_ai_brief(
    interview_id: int,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    iv = _load_interview_or_404(db, agency, interview_id)
    app_row = db.get(Application, iv.application_id)
    cand = db.get(CandidateProfile, iv.candidate_id)
    role = db.get(Role, iv.role_id)
    prior = (
        db.query(InterviewFeedback)
        .join(Interview, Interview.id == InterviewFeedback.interview_id)
        .filter(
            Interview.agency_id == agency.id,
            Interview.application_id == iv.application_id,
            InterviewFeedback.interview_id != iv.id,
        ).all()
    )
    brief = interviews_service.interview_brief(role, cand, app_row, iv, prior)
    return {"interview_id": iv.id, **brief}


# ═══════════════════════════════════════════════════════════════════════
# Recruiter OS — Phase 5 routes: Offers + Placements
# ═══════════════════════════════════════════════════════════════════════
offers_router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/agencies/{agency_id}/offers", tags=["recruiter: offers"]
)


@offers_router.post("", response_model=OfferOut, status_code=201)
def create_offer(
    payload: OfferCreate,
    agency: Agency = Depends(require_unlocked_agency),
    recruiter: Recruiter | None = Depends(_soft_recruiter),
    db: Session = Depends(get_db),
):
    app_row = _load_application_or_404(db, agency, payload.application_id)
    role = db.get(Role, app_row.role_id) if app_row.role_id else None
    offer = Offer(
        agency_id=agency.id, application_id=app_row.id,
        role_id=app_row.role_id, candidate_id=app_row.candidate_id,
        client_id=role.client_id if role else None,
        base_compensation=payload.base_compensation,
        bonus=payload.bonus, equity=payload.equity, allowances=payload.allowances,
        benefits=payload.benefits, start_date=payload.start_date,
        offer_date=payload.offer_date, expiry_date=payload.expiry_date,
        assigned_recruiter_id=recruiter.id if recruiter else None,
        notes=payload.notes,
    )
    db.add(offer); db.commit(); db.refresh(offer)
    return offer


@offers_router.get("", response_model=list[OfferOut])
def list_offers(
    agency: Agency = Depends(get_agency),
    role_id: int | None = Query(default=None),
    candidate_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(Offer).filter(Offer.agency_id == agency.id)
    if role_id is not None: q = q.filter(Offer.role_id == role_id)
    if candidate_id is not None: q = q.filter(Offer.candidate_id == candidate_id)
    return q.order_by(Offer.id.desc()).all()


@offers_router.patch("/{offer_id}", response_model=OfferOut)
def update_offer(
    offer_id: int,
    payload: OfferUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    offer = db.get(Offer, offer_id)
    if offer is None or offer.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Offer not found")
    from app.recruiter.enums import OfferStatus
    for f in ("base_compensation", "bonus", "equity", "allowances", "benefits",
              "start_date", "offer_date", "expiry_date", "notes"):
        v = getattr(payload, f)
        if v is not None:
            setattr(offer, f, v)
    if payload.status:
        try: offer.status = OfferStatus(payload.status)
        except ValueError: raise HTTPException(400, "Invalid offer status")
    db.commit(); db.refresh(offer)
    return offer


@offers_router.post(
    "/{offer_id}/negotiations", response_model=OfferNegotiationOut, status_code=201,
)
def add_offer_negotiation(
    offer_id: int,
    payload: OfferNegotiationCreate,
    agency: Agency = Depends(require_unlocked_agency),
    recruiter: Recruiter | None = Depends(_soft_recruiter),
    db: Session = Depends(get_db),
):
    offer = db.get(Offer, offer_id)
    if offer is None or offer.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Offer not found")
    n = OfferNegotiation(
        agency_id=agency.id, offer_id=offer_id,
        round_label=payload.round_label, from_party=payload.from_party,
        compensation=payload.compensation, comment=payload.comment,
        author_recruiter_id=recruiter.id if recruiter else None,
    )
    db.add(n); db.commit(); db.refresh(n)
    return n


@offers_router.get(
    "/{offer_id}/negotiations", response_model=list[OfferNegotiationOut],
)
def list_offer_negotiations(
    offer_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    offer = db.get(Offer, offer_id)
    if offer is None or offer.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Offer not found")
    return (
        db.query(OfferNegotiation)
        .filter(OfferNegotiation.offer_id == offer_id)
        .order_by(OfferNegotiation.id.asc()).all()
    )


placements_router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/agencies/{agency_id}/placements", tags=["recruiter: placements"]
)


@placements_router.post("", response_model=PlacementOut, status_code=201)
def create_placement(
    payload: PlacementCreate,
    agency: Agency = Depends(require_unlocked_agency),
    recruiter: Recruiter | None = Depends(_soft_recruiter),
    db: Session = Depends(get_db),
):
    role = db.get(Role, payload.role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(404, "Role not found")

    # One-active-placement-per-role guard. Cancelled / refunded / replaced
    # placements are ignored so a role can be re-filled after a fall-through.
    from app.recruiter.enums import PlacementStatus as _PS
    existing = (
        db.query(Placement)
        .filter(
            Placement.agency_id == agency.id,
            Placement.role_id == payload.role_id,
            Placement.status.in_([_PS.upcoming, _PS.started, _PS.completed]),
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Role already has an active placement (#{existing.id}, status: {existing.status.value}).",
        )

    fee_amount = payload.fee_amount
    if fee_amount is None:
        fee_amount = offers_service.compute_placement_fee(
            payload.final_compensation, payload.fee_percent,
        )
    guarantee_end = offers_service.guarantee_end_date(
        payload.start_date, payload.guarantee_weeks,
    )
    pl = Placement(
        agency_id=agency.id, offer_id=payload.offer_id,
        role_id=payload.role_id, candidate_id=payload.candidate_id,
        client_id=payload.client_id or role.client_id,
        recruiter_id=recruiter.id if recruiter else None,
        start_date=payload.start_date,
        final_compensation=payload.final_compensation,
        fee_percent=payload.fee_percent, fee_amount=fee_amount,
        guarantee_weeks=payload.guarantee_weeks,
        guarantee_ends_at=guarantee_end,
        notes=payload.notes,
    )
    db.add(pl); db.commit(); db.refresh(pl)
    # Seed default check-ins
    for c in offers_service.default_checkin_schedule(payload.start_date):
        due = None
        if c["due_date"]:
            from datetime import date as _date
            due = _date.fromisoformat(c["due_date"])
        db.add(PostPlacementCheckin(
            agency_id=agency.id, placement_id=pl.id,
            day_offset=c["day_offset"], due_date=due,
        ))
    db.commit()
    return pl


@placements_router.get("", response_model=list[PlacementOut])
def list_placements(
    agency: Agency = Depends(get_agency),
    role_id: int | None = Query(default=None),
    client_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(Placement).filter(Placement.agency_id == agency.id)
    if role_id is not None: q = q.filter(Placement.role_id == role_id)
    if client_id is not None: q = q.filter(Placement.client_id == client_id)
    return q.order_by(Placement.id.desc()).all()


@placements_router.patch("/{placement_id}", response_model=PlacementOut)
def update_placement(
    placement_id: int,
    payload: PlacementUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    pl = db.get(Placement, placement_id)
    if pl is None or pl.agency_id != agency.id:
        raise HTTPException(404, "Placement not found")
    from app.recruiter.enums import PlacementStatus
    for f in ("start_date", "final_compensation", "fee_percent", "fee_amount",
              "guarantee_weeks", "guarantee_ends_at", "notes"):
        v = getattr(payload, f)
        if v is not None:
            setattr(pl, f, v)
    if payload.status:
        try: pl.status = PlacementStatus(payload.status)
        except ValueError: raise HTTPException(400, "Invalid placement status")
    db.commit(); db.refresh(pl)
    return pl


@placements_router.get(
    "/{placement_id}/checkins", response_model=list[PostPlacementCheckinOut],
)
def list_placement_checkins(
    placement_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    pl = db.get(Placement, placement_id)
    if pl is None or pl.agency_id != agency.id:
        raise HTTPException(404, "Placement not found")
    return (
        db.query(PostPlacementCheckin)
        .filter(PostPlacementCheckin.placement_id == placement_id)
        .order_by(PostPlacementCheckin.day_offset.asc()).all()
    )


@placements_router.patch(
    "/{placement_id}/checkins/{checkin_id}", response_model=PostPlacementCheckinOut,
)
def update_placement_checkin(
    placement_id: int,
    checkin_id: int,
    payload: PostPlacementCheckinUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    recruiter: Recruiter | None = Depends(_soft_recruiter),
    db: Session = Depends(get_db),
):
    c = db.get(PostPlacementCheckin, checkin_id)
    if c is None or c.agency_id != agency.id or c.placement_id != placement_id:
        raise HTTPException(404, "Checkin not found")
    if payload.completed_at is not None:
        c.completed_at = payload.completed_at
        c.completed_by = recruiter.id if recruiter else None
    if payload.outcome is not None: c.outcome = payload.outcome
    if payload.notes is not None: c.notes = payload.notes
    db.commit(); db.refresh(c)
    return c


# ═══════════════════════════════════════════════════════════════════════
# Recruiter OS — Phase 6 routes: Tasks + Notifications + Daily brief
# ═══════════════════════════════════════════════════════════════════════
tasks_router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/agencies/{agency_id}/tasks", tags=["recruiter: tasks"]
)


@tasks_router.post("", response_model=RecruiterTaskOut, status_code=201)
def create_task(
    payload: RecruiterTaskCreate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    from app.recruiter.enums import TaskPriority
    try: prio = TaskPriority(payload.priority)
    except ValueError: raise HTTPException(400, "Invalid priority")
    t = RecruiterTask(
        agency_id=agency.id, owner_recruiter_id=payload.owner_recruiter_id,
        title=payload.title, detail=payload.detail, priority=prio,
        due_at=payload.due_at, parent_kind=payload.parent_kind,
        parent_id=payload.parent_id, ai_generated=payload.ai_generated,
    )
    db.add(t); db.commit(); db.refresh(t)
    return t


@tasks_router.get("", response_model=list[RecruiterTaskOut])
def list_tasks(
    agency: Agency = Depends(get_agency),
    status: str | None = Query(default=None),
    owner_recruiter_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(RecruiterTask).filter(RecruiterTask.agency_id == agency.id)
    if status is not None: q = q.filter(RecruiterTask.status == status)
    if owner_recruiter_id is not None:
        q = q.filter(RecruiterTask.owner_recruiter_id == owner_recruiter_id)
    return q.order_by(RecruiterTask.due_at.is_(None), RecruiterTask.due_at.asc(), RecruiterTask.id.desc()).all()


@tasks_router.patch("/{task_id}", response_model=RecruiterTaskOut)
def update_task(
    task_id: int,
    payload: RecruiterTaskUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    t = db.get(RecruiterTask, task_id)
    if t is None or t.agency_id != agency.id:
        raise HTTPException(404, "Task not found")
    from app.recruiter.enums import TaskStatus, TaskPriority
    if payload.title is not None: t.title = payload.title
    if payload.detail is not None: t.detail = payload.detail
    if payload.owner_recruiter_id is not None: t.owner_recruiter_id = payload.owner_recruiter_id
    if payload.due_at is not None: t.due_at = payload.due_at
    if payload.status is not None:
        try: t.status = TaskStatus(payload.status)
        except ValueError: raise HTTPException(400, "Invalid task status")
        if t.status == TaskStatus.done and t.completed_at is None:
            t.completed_at = datetime.utcnow()
    if payload.priority is not None:
        try: t.priority = TaskPriority(payload.priority)
        except ValueError: raise HTTPException(400, "Invalid task priority")
    db.commit(); db.refresh(t)
    return t


notifications_router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/agencies/{agency_id}/notifications", tags=["recruiter: notifications"]
)


@notifications_router.get("", response_model=list[NotificationOut])
def list_notifications(
    agency: Agency = Depends(get_agency),
    unread_only: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    q = db.query(Notification).filter(Notification.agency_id == agency.id)
    if unread_only: q = q.filter(Notification.is_read == False)  # noqa: E712
    return q.order_by(Notification.created_at.desc()).limit(200).all()


@notifications_router.post("/mark", response_model=int)
def mark_notifications(
    payload: NotificationBulkUpdate,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    n = (
        db.query(Notification)
        .filter(
            Notification.agency_id == agency.id,
            Notification.id.in_(payload.ids),
        )
        .update({"is_read": payload.is_read}, synchronize_session=False)
    )
    db.commit()
    return n


daily_brief_router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/agencies/{agency_id}/daily-brief", tags=["recruiter: daily brief"]
)


@daily_brief_router.get("", response_model=DailyBriefOut)
def daily_brief(
    agency: Agency = Depends(get_agency),
    recruiter: Recruiter | None = Depends(_soft_recruiter),
    db: Session = Depends(get_db),
):
    return productivity_service.daily_brief(
        db, agency.id, recruiter.id if recruiter else None,
    )


# ═══════════════════════════════════════════════════════════════════════
# Recruiter OS — Phase 7 routes: AI intelligence
# ═══════════════════════════════════════════════════════════════════════
intelligence_router = APIRouter(
    route_class=PublicIdRoute,
    prefix="/agencies/{agency_id}/intelligence", tags=["recruiter: intelligence"]
)


@intelligence_router.post("/ask-pool", response_model=AskPoolResult)
def ask_the_pool(
    payload: AskPoolRequest,
    agency: Agency = Depends(require_unlocked_agency),
    db: Session = Depends(get_db),
):
    return intelligence_service.ask_the_pool(db, agency.id, payload.query, payload.limit)


@intelligence_router.get("/roles/{role_id}/quality-check", response_model=RoleQualityCheckOut)
def role_quality_check(
    role_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(404, "Role not found")
    return intelligence_service.role_quality_check(db, agency.id, role)


@intelligence_router.get("/roles/{role_id}/health", response_model=RoleHealthOut)
def role_health(
    role_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(404, "Role not found")
    return intelligence_service.role_health(db, agency.id, role_id)


@intelligence_router.get("/clients/{client_id}/intelligence", response_model=ClientIntelligenceOut)
def client_intelligence_route(
    client_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    client = db.get(Client, client_id)
    if client is None or client.agency_id != agency.id:
        raise HTTPException(404, "Client not found")
    return intelligence_service.client_intelligence(db, agency.id, client_id)


def _public_submissions(db: Session, role: Role) -> list[PublicSubmission]:
    """Recruiter-approved submissions for this role, client-safe payload.
    Filters through the same visibility rules as the recruiter UI would."""
    subs = (
        db.query(ClientSubmission)
        .filter(
            ClientSubmission.agency_id == role.agency_id,
            ClientSubmission.role_id == role.id,
            ClientSubmission.status.in_([
                "submitted", "client_reviewing", "progressed", "on_hold",
            ]),
        )
        .order_by(ClientSubmission.submitted_at.desc(), ClientSubmission.id.desc())
        .all()
    )
    if not subs:
        return []
    cand_ids = {s.candidate_id for s in subs}
    cand_map = {
        c.id: c for c in db.query(CandidateProfile).filter(CandidateProfile.id.in_(cand_ids)).all()
    }
    apps = {
        a.id: a
        for a in db.query(Application).filter(
            Application.id.in_([s.application_id for s in subs])
        ).all()
    }

    out: list[PublicSubmission] = []
    for sub in subs:
        cand = cand_map.get(sub.candidate_id)
        app_row = apps.get(sub.application_id)
        # Enforce client visibility from the underlying application, not the sub.
        # Snapshotted fields on the submission take precedence for structured data.
        vmap = (app_row.client_visibility if app_row else None) or {}
        def allowed(field: str) -> bool:
            if field not in vmap:
                # default visible (Phase 1 defaults) unless field is off-limit
                return True
            return bool(vmap.get(field))

        out.append(PublicSubmission(
            submission_id=sub.id,
            candidate_id=sub.candidate_id,
            display_name=_client_display_name(cand.full_name if cand else None, sub.candidate_id),
            headline=cand.headline if cand else None,
            recruiter_summary=sub.client_summary if allowed("recruiter_summary") else None,
            key_strengths=sub.key_strengths or [],
            potential_gaps=sub.potential_gaps or [],
            expected_compensation=sub.compensation_snapshot if allowed("expected_compensation") else None,
            notice_period=sub.notice_period_snapshot if allowed("notice_period") else None,
            availability_date=(sub.availability_snapshot or {}).get("date") if allowed("availability") else None,
            availability_immediate=(sub.availability_snapshot or {}).get("immediate") if allowed("availability") else None,
            preferred_work_model=None,  # not in snapshot yet; keeps payload safe
            preferred_location=None,
            submitted_at=sub.submitted_at,
            client_decision=sub.client_decision.value if getattr(sub.client_decision, "value", None) else sub.client_decision,
        ))
    return out


@public_router.post(
    "/roles/{token}/submissions/{submission_id}/decision",
    response_model=PublicSubmission,
)
def public_submission_decision(
    token: str,
    submission_id: int,
    payload: PublicSubmissionDecision,
    db: Session = Depends(get_db),
):
    """Client posts a decision on an approved submission through the public
    share link. Writes a SubmissionFeedback row and mirrors the decision +
    responded_at onto the submission (drives the recruiter SLA metrics).
    """
    tok = (
        db.query(RoleShareToken)
        .filter(RoleShareToken.token == token, RoleShareToken.is_active.is_(True))
        .first()
    )
    if tok is None:
        raise HTTPException(status_code=404, detail="Share link is not active")
    sub = db.get(ClientSubmission, submission_id)
    if sub is None or sub.agency_id != tok.agency_id or sub.role_id != tok.role_id:
        raise HTTPException(status_code=404, detail="Submission not on this shared role")
    from app.recruiter.enums import ClientDecision
    try:
        decision = ClientDecision(payload.decision)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid decision")

    db.add(SubmissionFeedback(
        agency_id=sub.agency_id, submission_id=sub.id, decision=decision,
        reasons=payload.reasons, comment=payload.comment,
        client_contact_name=payload.client_contact_name,
        submitted_via="portal",
    ))
    sub.client_decision = decision
    if sub.client_responded_at is None:
        sub.client_responded_at = datetime.utcnow()
    if sub.client_viewed_at is None:
        sub.client_viewed_at = datetime.utcnow()
    db.commit(); db.refresh(sub)

    # Return the fresh client-view of just this submission for the UI.
    role = db.get(Role, sub.role_id)
    only = [x for x in _public_submissions(db, role) if x.submission_id == sub.id]
    if only:
        return only[0]
    raise HTTPException(status_code=500, detail="Submission update failed")



# ── Placement journey (used by role detail + public share when filled) ──
def _placement_journey_payload(db: Session, role: Role) -> dict:
    """
    Consolidated timeline for the placed candidate on this role.
    Returns None if the role has no active placement.
    """
    from app.recruiter.enums import PlacementStatus as _PS
    pl = (
        db.query(Placement)
        .filter(
            Placement.agency_id == role.agency_id,
            Placement.role_id == role.id,
            Placement.status.in_([_PS.upcoming, _PS.started, _PS.completed]),
        )
        .order_by(Placement.id.desc())
        .first()
    )
    if pl is None:
        return None

    cand = db.get(CandidateProfile, pl.candidate_id)
    # Application row for the placed candidate (there should be exactly one per
    # candidate+role).
    app_row = (
        db.query(Application)
        .filter(
            Application.agency_id == role.agency_id,
            Application.role_id == role.id,
            Application.candidate_id == pl.candidate_id,
        )
        .order_by(Application.id.desc())
        .first()
    )
    subs = (
        db.query(ClientSubmission)
        .filter(
            ClientSubmission.agency_id == role.agency_id,
            ClientSubmission.role_id == role.id,
            ClientSubmission.candidate_id == pl.candidate_id,
        )
        .order_by(ClientSubmission.id.asc())
        .all()
    )
    ivs = (
        db.query(Interview)
        .filter(
            Interview.agency_id == role.agency_id,
            Interview.role_id == role.id,
            Interview.candidate_id == pl.candidate_id,
        )
        .order_by(Interview.confirmed_time.asc(), Interview.id.asc())
        .all()
    )
    iv_ids = [iv.id for iv in ivs]
    fbs = (
        db.query(InterviewFeedback)
        .filter(
            InterviewFeedback.agency_id == role.agency_id,
            InterviewFeedback.interview_id.in_(iv_ids) if iv_ids else False,
        )
        .all()
    ) if iv_ids else []
    fb_by_iv: dict[int, list] = {}
    for f in fbs:
        fb_by_iv.setdefault(f.interview_id, []).append(f)

    offers = (
        db.query(Offer)
        .filter(
            Offer.agency_id == role.agency_id,
            Offer.role_id == role.id,
            Offer.candidate_id == pl.candidate_id,
        )
        .order_by(Offer.id.asc())
        .all()
    )

    return {
        "candidate": {
            "id": cand.id if cand else pl.candidate_id,
            "display_name": _client_display_name(cand.full_name if cand else None, pl.candidate_id),
            "full_name": cand.full_name if cand else None,
            "headline": cand.headline if cand else None,
        },
        "screening": {
            "outcome": app_row.screening_outcome.value if (app_row and app_row.screening_outcome) else None,
            "completed_at": app_row.screening_completed_at.isoformat() if (app_row and app_row.screening_completed_at) else None,
            "recruiter_summary": app_row.recruiter_summary if app_row else None,
        },
        "submissions": [
            {
                "id": sub.id,
                "status": sub.status.value if getattr(sub.status, "value", None) else sub.status,
                "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
                "client_decision": sub.client_decision.value if getattr(sub.client_decision, "value", None) else sub.client_decision,
                "client_responded_at": sub.client_responded_at.isoformat() if sub.client_responded_at else None,
                "client_summary": sub.client_summary,
            }
            for sub in subs
        ],
        "interviews": [
            {
                "id": iv.id,
                "stage": iv.stage.value if iv.stage else None,
                "interview_type": iv.interview_type.value if iv.interview_type else None,
                "status": iv.status.value if iv.status else None,
                "confirmed_time": iv.confirmed_time.isoformat() if iv.confirmed_time else None,
                "duration_minutes": iv.duration_minutes,
                "feedback": [
                    {
                        "interviewer_name": f.interviewer_name,
                        "decision": f.decision.value if getattr(f.decision, "value", None) else f.decision,
                        "comment": f.comment,
                    }
                    for f in fb_by_iv.get(iv.id, [])
                ],
            }
            for iv in ivs
        ],
        "offers": [
            {
                "id": o.id,
                "status": o.status.value if getattr(o.status, "value", None) else o.status,
                "base_compensation": o.base_compensation,
                "bonus": o.bonus,
                "start_date": o.start_date.isoformat() if o.start_date else None,
                "offer_date": o.offer_date.isoformat() if o.offer_date else None,
            }
            for o in offers
        ],
        "placement": {
            "id": pl.id,
            "status": pl.status.value if getattr(pl.status, "value", None) else pl.status,
            "start_date": pl.start_date.isoformat() if pl.start_date else None,
            "final_compensation": pl.final_compensation,
            "fee_percent": pl.fee_percent,
            "fee_amount": pl.fee_amount,
            "guarantee_weeks": pl.guarantee_weeks,
            "guarantee_ends_at": pl.guarantee_ends_at.isoformat() if pl.guarantee_ends_at else None,
        },
    }


@roles_router.get("/{role_id}/placement-journey")
def role_placement_journey(
    role_id: int,
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    role = _load_role(db, agency, role_id)
    payload = _placement_journey_payload(db, role)
    if payload is None:
        raise HTTPException(status_code=404, detail="No active placement for this role")
    return payload

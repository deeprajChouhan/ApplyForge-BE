"""Recruiter API routes (Phase 1). All agency-scoped routes enforce tenant
isolation through the get_agency dependency."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.recruiter.api.deps import get_agency, require_agency_feature, require_unlocked_agency
from app.recruiter.enums import ApplicationStage
from app.recruiter.models import (
    Agency,
    Application,
    CandidateProfile,
    Client,
    MarketSnapshot,
    Role,
    Shortlist,
    ShortlistEntry,
)
from app.recruiter.schemas import (
    ApplicationCreate,
    ApplicationOut,
    ApplicationStageUpdate,
    AssignCandidatesRequest,
    AssignCandidatesResult,
    CandidateBudgetUpdate,
    CandidateDetailOut,
    CandidateOut,
    CandidateRoleMatchesOut,
    ClientCreate,
    ClientOut,
    ConvertRequest,
    ConvertResult,
    IngestResult,
    IngestResultItem,
    JobListingOut,
    MarketOverviewOut,
    MarketSnapshotOut,
    NextHireAdvisoryOut,
    NextHireSuggestionOut,
    RoleBoardColumn,
    RoleBoardOut,
    RoleCreate,
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
from app.recruiter.services.ingestion import ingest_batch
from app.recruiter.services.listing import generate_listing
from app.recruiter.services.market import compute_market
from app.recruiter.services.market_crawler import crawl_role_market
from app.recruiter.services.matching import embed_role
from app.recruiter.services.placement import rank_roles_for_candidate
from app.recruiter.services.shortlist import generate_shortlist
from app.recruiter.services.skills import normalize_skill
from app.recruiter.services.swot import compute_swot

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
    db: Session = Depends(get_db),
):
    app_row = db.get(Application, application_id)
    if app_row is None or app_row.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Application not found")
    app_row.stage = payload.stage
    app_row.last_activity_at = datetime.utcnow()
    db.commit()
    db.refresh(app_row)
    return app_row


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


# ── Market analytics (self-contained over the agency's own data) ──────────
market_router = APIRouter(prefix="/agencies/{agency_id}/market", tags=["recruiter: market"])


@market_router.get("", response_model=MarketOverviewOut)
def market_overview(
    agency: Agency = Depends(require_agency_feature("market")),
    db: Session = Depends(get_db),
):
    """Demand vs supply, skill shortages, salary bands, and pipeline health."""
    return MarketOverviewOut(**asdict(compute_market(db, agency.id)))

"""Recruiter API routes (Phase 1). All agency-scoped routes enforce tenant
isolation through the get_agency dependency."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.recruiter.api.deps import get_agency
from app.recruiter.models import Agency, Application, CandidateProfile, Client, Role, Shortlist
from app.recruiter.schemas import (
    ApplicationCreate,
    ApplicationOut,
    ApplicationStageUpdate,
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
    NextHireAdvisoryOut,
    NextHireSuggestionOut,
    RoleCreate,
    RoleMatchOut,
    RoleOut,
    ShortlistOut,
)
from app.recruiter.bridge import provision_candidate
from app.recruiter.services.advisory import next_hire_advisory
from app.recruiter.services.ingestion import ingest_batch
from app.recruiter.services.listing import generate_listing
from app.recruiter.services.market import compute_market
from app.recruiter.services.matching import embed_role
from app.recruiter.services.placement import rank_roles_for_candidate
from app.recruiter.services.shortlist import generate_shortlist
from app.recruiter.services.skills import normalize_skill

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
    agency: Agency = Depends(get_agency),
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
def client_next_hire(client_id: int, agency: Agency = Depends(get_agency), db: Session = Depends(get_db)):
    """Advisory: infer this client's likely next hire from their roster + benchmarks."""
    client = _load_client(db, agency, client_id)
    advisory = next_hire_advisory(db, agency.id, client)
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
    agency: Agency = Depends(get_agency),
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
    )
    db.add(role)
    db.flush()
    role.embedding = embed_role(role)
    db.commit()
    db.refresh(role)
    return role


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
def draft_listing(role_id: int, agency: Agency = Depends(get_agency), db: Session = Depends(get_db)):
    """Draft a job listing for this role, grounded in the agency's pool patterns."""
    role = db.get(Role, role_id)
    if role is None or role.agency_id != agency.id:
        raise HTTPException(status_code=404, detail="Role not found")
    listing = generate_listing(db, role)
    return JobListingOut(**listing.__dict__)


# ── Candidates + ingestion ───────────────────────────────────────────────
candidates_router = APIRouter(
    prefix="/agencies/{agency_id}/candidates", tags=["recruiter: candidates"]
)


@candidates_router.post("/ingest", response_model=IngestResult, status_code=201)
async def ingest_cvs(
    files: list[UploadFile] = File(...),
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    """Bulk-CV ingestion: parse each uploaded CV into the agency's pool."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    payload: list[tuple[str, bytes]] = []
    for f in files:
        payload.append((f.filename or "cv.txt", await f.read()))

    ingested = ingest_batch(db, agency.id, payload)
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


@candidates_router.get("/{candidate_id}", response_model=CandidateOut)
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
    agency: Agency = Depends(get_agency),
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
    agency: Agency = Depends(get_agency),
    db: Session = Depends(get_db),
):
    """Run inverted matching for this role and save the ranked shortlist."""
    role = _load_role(db, agency, role_id)
    return generate_shortlist(db, role, limit=limit)


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
    agency: Agency = Depends(get_agency),
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
    agency: Agency = Depends(get_agency),
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


# ── Market analytics (self-contained over the agency's own data) ──────────
market_router = APIRouter(prefix="/agencies/{agency_id}/market", tags=["recruiter: market"])


@market_router.get("", response_model=MarketOverviewOut)
def market_overview(agency: Agency = Depends(get_agency), db: Session = Depends(get_db)):
    """Demand vs supply, skill shortages, salary bands, and pipeline health."""
    return MarketOverviewOut(**asdict(compute_market(db, agency.id)))

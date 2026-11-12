"""
Partner API v1 routes — mounted at /api/v1/recruiter/partner/v1.

Every route resolves a PartnerContext (see deps.py) and filters every query by
`ctx.agency_id`; client-scoped keys are further narrowed to their client's
jobs and to applications they submitted themselves. Ids crossing the boundary
are opaque GUIDs (PublicIdRoute).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.recruiter.enums import ApplicationStage, CandidateSource, NotificationKind, RoleStatus, UsageKind
from app.recruiter.ids import PublicIdRoute
from app.recruiter.models import (
    ApiRequestLog,
    Application,
    ApplicationNote,
    CandidateDocument,
    CandidateProfile,
    Client,
    IdempotencyRecord,
    Interview,
    Notification,
    Offer,
    Placement,
    Role,
    Shortlist,
)
from app.recruiter.partner import jobs as partner_jobs
from app.recruiter.partner import keys as keys_mod
from app.recruiter.partner import schemas as S
from app.recruiter.partner import webhooks
from app.recruiter.partner.deps import (
    PartnerContext,
    enforce_ip_limit,
    get_partner_context,
    require_agency_key,
    require_scope,
    require_write_scope,
)
from app.recruiter.services import documents as documents_service
from app.recruiter.services import pipeline as pipeline_service
from app.recruiter.services import usage as usage_service
from app.recruiter.services.erasure import erase_candidate
from app.recruiter.services.matching import embed_role
from app.recruiter.services.pipeline import STAGE_ORDER
from app.recruiter.services.skills import normalize_skill

router = APIRouter(route_class=PublicIdRoute, prefix="/partner/v1", tags=["recruiter: partner api"])

MAX_PAGE = 100


def _err(code: int, error: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"error": error, "message": message})


def _page(total_fetched: int, limit: int, offset: int) -> dict:
    more = total_fetched > limit
    return {"has_more": more, "next_offset": offset + limit if more else None}


def _norm_skills(skills: list[str] | None) -> list[str]:
    return list(dict.fromkeys(normalize_skill(s) for s in (skills or []) if s and normalize_skill(s)))


# ── Idempotency ─────────────────────────────────────────────────────────
def _idem_key(request: Request) -> str | None:
    k = request.headers.get("idempotency-key")
    if k is None:
        return None
    k = k.strip()
    if not (1 <= len(k) <= 128) or not all(c.isalnum() or c in "-_.:" for c in k):
        raise _err(400, "invalid_idempotency_key", "Idempotency-Key must be 1-128 chars [A-Za-z0-9-_.:].")
    return k


def _idem_replay(db: Session, ctx: PartnerContext, key: str | None, body_hash: str) -> JSONResponse | None:
    if not key:
        return None
    rec = (
        db.query(IdempotencyRecord)
        .filter(IdempotencyRecord.api_key_id == ctx.key.id, IdempotencyRecord.idem_key == key)
        .first()
    )
    if rec is None:
        return None
    if rec.request_hash != body_hash:
        raise _err(422, "idempotency_mismatch", "This Idempotency-Key was used with a different request.")
    return JSONResponse(status_code=rec.status_code, content=rec.response_body, headers={"Idempotent-Replayed": "true"})


def _idem_store(db: Session, ctx: PartnerContext, key: str | None, body_hash: str, status: int, body) -> None:
    if not key:
        return
    try:
        db.add(
            IdempotencyRecord(
                agency_id=ctx.agency_id,
                api_key_id=ctx.key.id,
                idem_key=key,
                request_hash=body_hash,
                status_code=status,
                response_body=jsonable_encoder(body),
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()


def _hash_json(obj) -> str:
    return hashlib.sha256(json.dumps(jsonable_encoder(obj), sort_keys=True).encode()).hexdigest()


# ── Scoping helpers ─────────────────────────────────────────────────────
def _jobs_q(db: Session, ctx: PartnerContext):
    q = db.query(Role).filter(Role.agency_id == ctx.agency_id)
    if ctx.client_id is not None:
        q = q.filter(Role.client_id == ctx.client_id)
    return q


def _load_job_for_write(db: Session, ctx: PartnerContext, job_id: int) -> Role:
    role = _load_job(db, ctx, job_id)
    _assert_client_may_write(ctx, role)
    return role


def _assert_client_may_write(ctx: PartnerContext, role: Role) -> None:
    """A client's portal may edit/close the jobs it pushed, not ones the agency created."""
    if ctx.client_id is not None and (role.source or "app") != "partner_api":
        raise _err(403, "agency_managed_job", "This job is managed by the agency and can't be changed from here.")


def _load_job(db: Session, ctx: PartnerContext, job_id: int) -> Role:
    role = db.get(Role, job_id)
    if role is None or role.agency_id != ctx.agency_id or (
        ctx.client_id is not None and role.client_id != ctx.client_id
    ):
        raise _err(404, "not_found", "Job not found.")
    return role


def _apps_q(db: Session, ctx: PartnerContext):
    q = db.query(Application).filter(Application.agency_id == ctx.agency_id)
    if ctx.client_id is not None:
        # A client-scoped key only sees applicants it brought in itself.
        q = q.filter(Application.intake_client_id == ctx.client_id)
    return q


def _load_app(db: Session, ctx: PartnerContext, application_id: int) -> Application:
    a = db.get(Application, application_id)
    if a is None or a.agency_id != ctx.agency_id or (
        ctx.client_id is not None and a.intake_client_id != ctx.client_id
    ):
        raise _err(404, "not_found", "Application not found.")
    return a


def _load_candidate(db: Session, ctx: PartnerContext, candidate_id: int) -> CandidateProfile:
    require_agency_key(ctx)
    c = db.get(CandidateProfile, candidate_id)
    if c is None or c.agency_id != ctx.agency_id:
        raise _err(404, "not_found", "Candidate not found.")
    return c


def _accepting(role: Role) -> bool:
    return role.status == RoleStatus.open and not role.is_draft


def _job_out(db: Session, role: Role, *, with_pipeline: bool = False, intake_client_id: int | None = None) -> S.JobOut:
    pipeline = None
    if with_pipeline:
        q = db.query(Application.stage, func.count(Application.id)).filter(
            Application.agency_id == role.agency_id, Application.role_id == role.id
        )
        if intake_client_id is not None:  # a client sees counts for its own applicants only
            q = q.filter(Application.intake_client_id == intake_client_id)
        rows = q.group_by(Application.stage).all()
        pipeline = {s.value: 0 for s in ApplicationStage}
        for stage, n in rows:
            pipeline[stage.value if hasattr(stage, "value") else str(stage)] = int(n)
    return S.JobOut(
        id=role.id,
        external_ref=role.external_ref,
        title=role.title,
        description=role.description,
        status=role.status,
        is_draft=bool(role.is_draft),
        employment_type=role.employment_type,
        location=role.location,
        country_code=role.country_code,
        seniority=role.seniority,
        required_skills=role.required_skills or [],
        preferred_skills=role.preferred_skills or [],
        min_years_experience=role.min_years_experience,
        salary_min=role.salary_min,
        salary_max=role.salary_max,
        currency=role.budget_currency,
        client_id=role.client_id,
        source=role.source or "app",
        accepting_applications=_accepting(role),
        created_at=role.created_at,
        updated_at=role.updated_at,
        pipeline=pipeline,
    )


def _public_job_out(role: Role) -> S.PublicJobOut:
    return S.PublicJobOut(
        id=role.id,
        title=role.title,
        description=role.description,
        employment_type=role.employment_type,
        location=role.location,
        country_code=role.country_code,
        seniority=role.seniority,
        required_skills=role.required_skills or [],
        preferred_skills=role.preferred_skills or [],
        salary_min=role.salary_min,
        salary_max=role.salary_max,
        currency=role.budget_currency,
        posted_at=role.created_at,
    )


def _app_out(a: Application) -> S.ApplicationOut:
    m = a.match_explanation or None
    return S.ApplicationOut(
        id=a.id,
        job_id=a.role_id,
        candidate_id=a.candidate_id,
        external_ref=a.external_ref,
        stage=a.stage,
        source=a.source or "app",
        processing_status=a.processing_status or "none",
        fit_score=a.fit_score,
        match=S.MatchExplanation(**m) if m else None,
        candidate_name=a.candidate.full_name if a.candidate else None,
        created_at=a.created_at,
        updated_at=a.updated_at,
        last_activity_at=a.last_activity_at,
    )


def _doc_out(d: CandidateDocument) -> S.DocumentOut:
    return S.DocumentOut(
        id=d.id, kind=d.kind, filename=d.original_filename, content_type=d.content_type,
        size_bytes=d.size_bytes, created_at=d.created_at,
    )


def _resolve_client_id(db: Session, ctx: PartnerContext, payload: S.JobWrite) -> int | None | type(...):
    """Returns the client id to set, or Ellipsis when the payload doesn't mention a client."""
    if ctx.client_id is not None:
        return ctx.client_id  # client-scoped keys can only create roles for their own client
    if payload.client_id is not None:
        c = db.get(Client, payload.client_id)
        if c is None or c.agency_id != ctx.agency_id:
            raise _err(422, "invalid_client", "client_id does not belong to this account.")
        return c.id
    if payload.client_external_ref:
        c = (
            db.query(Client)
            .filter(Client.agency_id == ctx.agency_id, Client.external_ref == payload.client_external_ref)
            .first()
        )
        if c is None:
            raise _err(422, "invalid_client", "No client with that client_external_ref. Create it first.")
        return c.id
    return ...


def _apply_job_fields(role: Role, payload: S.JobWrite) -> bool:
    data = payload.model_dump(exclude_unset=True, exclude={"client_id", "client_external_ref", "currency"})
    reembed = False
    for k in ("required_skills", "preferred_skills"):
        if k in data:
            data[k] = _norm_skills(data[k])
            reembed = True
    if any(k in data for k in ("title", "description", "seniority")):
        reembed = True
    for k, v in data.items():
        setattr(role, k, v)
    if payload.currency is not None:
        role.budget_currency = payload.currency
    if role.salary_min is not None and role.salary_max is not None and role.salary_min > role.salary_max:
        raise _err(422, "invalid_salary", "salary_min must not exceed salary_max.")
    return reembed


def _emit_job(db: Session, role: Role, event: str) -> None:
    webhooks.emit(
        db, role.agency_id, event,
        {"job_id": role.id, "status": role.status.value, "external_ref": role.external_ref},
        client_id=role.client_id,
    )


# ── Meta ────────────────────────────────────────────────────────────────
@router.get("/me", response_model=S.MeOut)
def me(ctx: PartnerContext = Depends(get_partner_context)):
    """Which account and permissions this key has. Use it as a connection test."""
    return S.MeOut(
        agency_id=ctx.agency_id,
        agency_name=ctx.agency.name,
        key_name=ctx.key.name,
        key_prefix=ctx.key.key_prefix,
        kind=ctx.key.kind,
        environment=ctx.key.environment,
        client_id=ctx.client_id,
        scopes=sorted(ctx.scopes),
        rate_limit_per_minute=ctx.rate.get("limit", 0),
    )


@router.get("/meta", response_model=S.MetaOut)
def meta(ctx: PartnerContext = Depends(get_partner_context)):  # noqa: ARG001
    return S.MetaOut(
        stages=[
            S.StageInfo(stage=s.value, order=i, terminal=s in (ApplicationStage.placed,))
            for i, s in enumerate(STAGE_ORDER)
        ] + [S.StageInfo(stage=ApplicationStage.rejected.value, order=len(STAGE_ORDER), terminal=True)],
        events=[S.EventInfo(event=k, description=v) for k, v in webhooks.EVENTS.items()],
        scopes=dict(keys_mod.SCOPE_DESCRIPTIONS),
    )


# ── Public jobs (careers page) ─────────────────────────────────────────
@router.get("/public/jobs", response_model=S.PublicJobList)
def public_jobs(
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
    ctx: PartnerContext = Depends(require_scope("jobs:read_public")),
    db: Session = Depends(get_db),
):
    rows = (
        _jobs_q(db, ctx)
        .filter(Role.status == RoleStatus.open, Role.is_draft.is_(False))
        .order_by(Role.id.desc())
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    return S.PublicJobList(data=[_public_job_out(r) for r in rows[:limit]], **_page(len(rows), limit, offset))


@router.get("/public/jobs/{job_id}", response_model=S.PublicJobOut)
def public_job(
    job_id: int,
    ctx: PartnerContext = Depends(require_scope("jobs:read_public")),
    db: Session = Depends(get_db),
):
    role = _load_job(db, ctx, job_id)
    if not _accepting(role):
        raise _err(404, "not_found", "Job not found.")
    return _public_job_out(role)


# ── Jobs ────────────────────────────────────────────────────────────────
@router.get("/jobs", response_model=S.JobList)
def list_jobs(
    status: RoleStatus | None = None,
    external_ref: str | None = Query(default=None, max_length=128),
    updated_since: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
    ctx: PartnerContext = Depends(require_scope("jobs:read")),
    db: Session = Depends(get_db),
):
    q = _jobs_q(db, ctx)
    if status is not None:
        q = q.filter(Role.status == status)
    if external_ref:
        q = q.filter(Role.external_ref == external_ref)
    if updated_since is not None:
        q = q.filter(Role.updated_at >= updated_since.replace(tzinfo=None))
    rows = q.order_by(Role.id.desc()).offset(offset).limit(limit + 1).all()
    return S.JobList(data=[_job_out(db, r) for r in rows[:limit]], **_page(len(rows), limit, offset))


@router.get("/jobs/{job_id}", response_model=S.JobOut)
def get_job(
    job_id: int,
    ctx: PartnerContext = Depends(require_scope("jobs:read")),
    db: Session = Depends(get_db),
):
    return _job_out(db, _load_job(db, ctx, job_id), with_pipeline=True, intake_client_id=ctx.client_id)


@router.post("/jobs", response_model=S.JobOut, status_code=201)
def create_job(
    payload: S.JobCreate,
    request: Request,
    background: BackgroundTasks,
    ctx: PartnerContext = Depends(require_write_scope("jobs:write")),
    db: Session = Depends(get_db),
):
    key = _idem_key(request)
    body_hash = _hash_json(payload.model_dump())
    replay = _idem_replay(db, ctx, key, body_hash)
    if replay:
        return replay
    client_id = _resolve_client_id(db, ctx, payload)
    if payload.external_ref and _jobs_q(db, ctx).filter(Role.external_ref == payload.external_ref).first():
        raise _err(409, "duplicate_external_ref", "A job with this external_ref exists. Use PUT /jobs/by-external-ref/{ref}.")
    if ctx.is_test:
        return JSONResponse(status_code=200, content={"test_mode": True, "valid": True})

    role = Role(agency_id=ctx.agency_id, title=payload.title, source="partner_api")
    role.client_id = None if client_id is ... else client_id
    _apply_job_fields(role, payload)
    if role.status is None:
        role.status = RoleStatus.open
    role.embedding = embed_role(role)
    db.add(role)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _err(409, "duplicate_external_ref", "A job with this external_ref already exists.")
    _emit_job(db, role, "job.created")
    db.commit()
    db.refresh(role)
    out = _job_out(db, role)
    _idem_store(db, ctx, key, body_hash, 201, out)
    _maybe_enrich_role(background, role)
    partner_jobs.dispatch_webhooks(background, ctx.agency_id)
    return out


def _maybe_enrich_role(background: BackgroundTasks, role: Role) -> None:
    if role.description and not role.required_skills:
        background.add_task(partner_jobs.enrich_role_from_description, role.id)


def _slug(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")


def _find_unlinked_role(db: Session, ctx: PartnerContext, external_ref: str, title: str | None) -> Role | None:
    """
    A careers site usually keys jobs by a slug of the title. If the agency
    already created that role in the app (so it has no external_ref yet),
    link to it instead of creating a duplicate. Only adopts when exactly one
    role matches — ambiguity falls through to create. Client-scoped keys may
    not take over app-created roles, so they never adopt.
    """
    if ctx.client_id is not None:
        return None
    ref_slug = _slug(external_ref)
    title_slug = _slug(title)
    rows = (
        db.query(Role)
        .filter(Role.agency_id == ctx.agency_id, Role.external_ref.is_(None))
        .all()
    )
    matches = [r for r in rows if _slug(r.title) in {ref_slug, title_slug} - {""}]
    open_matches = [r for r in matches if _accepting(r)]
    if len(open_matches) == 1:
        return open_matches[0]
    if not open_matches and len(matches) == 1:
        return matches[0]
    return None


def _only_missing_fields(role: Role, payload: S.JobWrite) -> S.JobWrite:
    """Drop payload fields the role already has a value for."""
    keep = {}
    for k in payload.model_fields_set:
        if k in ("client_id", "client_external_ref", "external_ref", "title"):
            continue
        attr = "budget_currency" if k == "currency" else k
        current = getattr(role, attr, None)
        if current in (None, "", []):
            keep[k] = getattr(payload, k)
    return S.JobWrite.model_construct(_fields_set=set(keep), **keep)


@router.put("/jobs/by-external-ref/{external_ref}", response_model=S.JobOut)
def upsert_job(
    external_ref: str,
    payload: S.JobWrite,
    background: BackgroundTasks,
    response: Response,
    ctx: PartnerContext = Depends(require_write_scope("jobs:write")),
    db: Session = Depends(get_db),
):
    """Create-or-update keyed by your own job id — the simplest way to keep a portal in sync."""
    try:
        external_ref = S._clean_ref(external_ref)
    except ValueError as exc:
        raise _err(422, "invalid_external_ref", str(exc))
    role = _jobs_q(db, ctx).filter(Role.external_ref == external_ref).first()
    if role is None and (
        db.query(Role).filter(Role.agency_id == ctx.agency_id, Role.external_ref == external_ref).first()
    ):
        # Exists for another client of this agency — don't reveal or clobber it.
        raise _err(409, "duplicate_external_ref", "This external_ref is already in use.")
    adopted = False
    if role is None:
        role = _find_unlinked_role(db, ctx, external_ref, payload.title)
        adopted = role is not None
    if role is not None and not adopted:
        _assert_client_may_write(ctx, role)
    client_id = _resolve_client_id(db, ctx, payload)
    if role is None and not payload.title:
        raise _err(422, "missing_title", "title is required when creating a job.")
    if ctx.is_test:
        return JSONResponse(status_code=200, content={"test_mode": True, "valid": True})

    created = role is None
    prev_status = role.status if role else None
    if created:
        role = Role(agency_id=ctx.agency_id, title=payload.title, source="partner_api", status=RoleStatus.open)
        db.add(role)
    role.external_ref = external_ref
    if adopted:
        # First sync against a role the agency built in the app: link it, but
        # only fill gaps — never overwrite what the recruiter wrote.
        if client_id is not ... and role.client_id is None:
            role.client_id = client_id
        reembed = _apply_job_fields(role, _only_missing_fields(role, payload))
    else:
        if client_id is not ...:
            role.client_id = client_id
        reembed = _apply_job_fields(role, payload)
    if created or reembed:
        role.embedding = embed_role(role)
    db.flush()
    if created:
        _emit_job(db, role, "job.created")
    else:
        _emit_job(db, role, "job.updated")
        if prev_status == RoleStatus.open and role.status != RoleStatus.open:
            _emit_job(db, role, "job.closed")
    db.commit()
    db.refresh(role)
    response.status_code = 201 if created else 200
    _maybe_enrich_role(background, role)
    partner_jobs.dispatch_webhooks(background, ctx.agency_id)
    return _job_out(db, role)


@router.patch("/jobs/{job_id}", response_model=S.JobOut)
def update_job(
    job_id: int,
    payload: S.JobWrite,
    background: BackgroundTasks,
    ctx: PartnerContext = Depends(require_write_scope("jobs:write")),
    db: Session = Depends(get_db),
):
    role = _load_job_for_write(db, ctx, job_id)
    if payload.external_ref and payload.external_ref != role.external_ref and (
        db.query(Role).filter(Role.agency_id == ctx.agency_id, Role.external_ref == payload.external_ref).first()
    ):
        raise _err(409, "duplicate_external_ref", "This external_ref is already in use.")
    client_id = _resolve_client_id(db, ctx, payload)
    if ctx.is_test:
        return JSONResponse(status_code=200, content={"test_mode": True, "valid": True})
    prev_status = role.status
    if client_id is not ...:
        role.client_id = client_id
    if _apply_job_fields(role, payload):
        role.embedding = embed_role(role)
    _emit_job(db, role, "job.updated")
    if prev_status == RoleStatus.open and role.status != RoleStatus.open:
        _emit_job(db, role, "job.closed")
    db.commit()
    db.refresh(role)
    partner_jobs.dispatch_webhooks(background, ctx.agency_id)
    return _job_out(db, role)


@router.post("/jobs/{job_id}/close", response_model=S.JobOut)
def close_job(
    job_id: int,
    background: BackgroundTasks,
    status: RoleStatus = Query(default=RoleStatus.closed),
    ctx: PartnerContext = Depends(require_write_scope("jobs:write")),
    db: Session = Depends(get_db),
):
    if status == RoleStatus.open:
        raise _err(422, "invalid_status", "Use PATCH to reopen a job.")
    role = _load_job_for_write(db, ctx, job_id)
    if ctx.is_test:
        return JSONResponse(status_code=200, content={"test_mode": True, "valid": True})
    if role.status != status:
        role.status = status
        _emit_job(db, role, "job.closed")
        db.commit()
        db.refresh(role)
        partner_jobs.dispatch_webhooks(background, ctx.agency_id)
    return _job_out(db, role)


@router.post("/jobs/parse", response_model=S.JobParseOut)
def parse_job_text(
    payload: S.JobParseIn,
    ctx: PartnerContext = Depends(require_write_scope("jobs:write")),
    db: Session = Depends(get_db),
):
    """Turn pasted JD text into a structured draft (nothing is saved)."""
    if ctx.is_test:  # no LLM spend for sandbox keys
        return S.JobParseOut(used_llm=False, draft={})
    from app.recruiter.services.jd_parse import parse_jd

    draft = parse_jd(payload.text)
    if draft.get("used_llm"):
        usage_service.record(db, ctx.agency_id, UsageKind.api_jd_parsed)
    used = bool(draft.pop("used_llm", False))
    draft.pop("notes", None)  # internal-angle note — not for external systems
    if "budget_currency" in draft:
        draft["currency"] = draft.pop("budget_currency")
    return S.JobParseOut(used_llm=used, draft=draft)


# ── Applications: intake ────────────────────────────────────────────────
def _verify_captcha(token: str | None, ip: str | None) -> bool:
    secret = settings.partner_turnstile_secret
    if not secret:
        return True
    if not token or len(token) > 4096:
        return False
    try:
        import httpx

        r = httpx.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": secret.get_secret_value(), "response": token, **({"remoteip": ip} if ip else {})},
            timeout=5.0,
        )
        return bool(r.json().get("success"))
    except Exception:
        return False


@router.post("/jobs/{job_id}/applications", status_code=202)
async def create_application(
    job_id: int,
    request: Request,
    background: BackgroundTasks,
    resume: UploadFile = File(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    consent: str = Form(...),
    privacy_notice_version: str = Form(...),
    phone: str | None = Form(default=None),
    linkedin_url: str | None = Form(default=None),
    location: str | None = Form(default=None),
    cover_letter: str | None = Form(default=None),
    answers: str | None = Form(default=None),
    consent_text: str | None = Form(default=None),
    source_site: str | None = Form(default=None),
    utm_source: str | None = Form(default=None),
    utm_medium: str | None = Form(default=None),
    utm_campaign: str | None = Form(default=None),
    external_ref: str | None = Form(default=None),
    captcha_token: str | None = Form(default=None),
    ctx: PartnerContext = Depends(require_write_scope("applications:create")),
    db: Session = Depends(get_db),
):
    """
    Submit a careers-site application into the job's pipeline at `sourced`.
    multipart/form-data with a `resume` file (PDF/DOCX/DOC/TXT, ≤10 MB).
    Returns 202 — CV parsing and fit scoring run in the background and
    `application.processed` fires when they finish.
    """
    if ctx.is_publishable:
        enforce_ip_limit(ctx, "apply", settings.partner_apply_per_ip_per_hour)
        if not _verify_captcha(captcha_token, ctx.ip):
            raise _err(400, "captcha_failed", "Captcha verification failed.")

    try:
        parsed_answers = json.loads(answers) if answers else None
    except ValueError:
        raise _err(422, "invalid_answers", "answers must be a JSON array of {question, answer}.")
    try:
        form = S.ApplicantForm(
            first_name=first_name, last_name=last_name, email=email,
            consent=str(consent).strip().lower() in ("true", "1", "yes", "on"),
            privacy_notice_version=privacy_notice_version, phone=phone, linkedin_url=linkedin_url,
            location=location, cover_letter=cover_letter, answers=parsed_answers,
            consent_text=consent_text, source_site=source_site, utm_source=utm_source,
            utm_medium=utm_medium, utm_campaign=utm_campaign, external_ref=external_ref,
        )
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise _err(422, "invalid_application", f"Invalid fields: {', '.join(fields)}")

    content = await resume.read(documents_service.MAX_DOCUMENT_BYTES + 1)
    try:
        vfile = documents_service.validate_upload(resume.filename, content)
    except documents_service.DocumentRejected as exc:
        code = 413 if len(content) > documents_service.MAX_DOCUMENT_BYTES else 400
        raise _err(code, "invalid_resume", str(exc))

    role = _load_job(db, ctx, job_id)
    if not _accepting(role):
        raise _err(409, "job_closed", "This job is not accepting applications.")

    idem = None if ctx.is_publishable else _idem_key(request)
    body_hash = _hash_json({"job": job_id, "form": form.model_dump(), "file": vfile.sha256})
    replay = _idem_replay(db, ctx, idem, body_hash)
    if replay:
        return replay
    if ctx.is_test:
        return JSONResponse(status_code=200, content={"test_mode": True, "valid": True})

    email_l = str(form.email).lower()
    pool_match = False
    if ctx.client_id is not None:
        # A client-scoped key only ever dedupes against profiles that ITS OWN
        # submissions created. Linking to an agency-sourced profile would let a
        # client read the agency's record (name, score, match evidence) and
        # claim the candidate outside the agency's consent/fee flow.
        cand = (
            db.query(CandidateProfile)
            .join(Application, Application.candidate_id == CandidateProfile.id)
            .filter(
                CandidateProfile.agency_id == ctx.agency_id,
                Application.agency_id == ctx.agency_id,
                Application.intake_client_id == ctx.client_id,
                func.lower(CandidateProfile.email) == email_l,
            )
            .order_by(CandidateProfile.id)
            .first()
        )
        if cand is None:
            pool_match = (
                db.query(CandidateProfile.id)
                .filter(CandidateProfile.agency_id == ctx.agency_id, func.lower(CandidateProfile.email) == email_l)
                .first()
                is not None
            )
    else:
        cand = (
            db.query(CandidateProfile)
            .filter(CandidateProfile.agency_id == ctx.agency_id, func.lower(CandidateProfile.email) == email_l)
            .order_by(CandidateProfile.id)
            .first()
        )
    new_profile = cand is None
    if cand is not None:
        existing = pipeline_service.find_application(db, ctx.agency_id, role.id, cand.id)
        if existing is not None:
            if ctx.is_publishable:
                return S.ApplicationReceipt()
            out = S.ApplicationCreated(
                application_id=existing.id, candidate_id=cand.id, job_id=role.id,
                stage=existing.stage, processing_status=existing.processing_status or "none",
                duplicate=True,
            )
            return JSONResponse(status_code=200, content=jsonable_encoder(out))
    else:
        cand = CandidateProfile(
            agency_id=ctx.agency_id,
            source=CandidateSource.careers_site if ctx.is_publishable else CandidateSource.partner_api,
            full_name=f"{form.first_name} {form.last_name}".strip(),
            email=email_l,
            phone=form.phone,
            location=form.location,
            linkedin_url=form.linkedin_url,
            source_file=vfile.filename,
        )
        db.add(cand)
        db.flush()

    now = datetime.utcnow()
    app_row = Application(
        agency_id=ctx.agency_id,
        candidate_id=cand.id,
        role_id=role.id,
        job_title=role.title,
        stage=ApplicationStage.sourced,
        source="partner_api",
        external_ref=form.external_ref,
        processing_status="queued",
        cover_letter=form.cover_letter,
        form_answers=form.answers,
        intake_api_key_id=ctx.key.id,
        intake_client_id=ctx.client_id,
        source_detail={
            "channel": "careers_site" if ctx.is_publishable else "server",
            "key_prefix": ctx.key.key_prefix,
            "site": form.source_site,
            "origin": ctx.origin,
            "utm": {k: v for k, v in {
                "source": form.utm_source, "medium": form.utm_medium, "campaign": form.utm_campaign,
            }.items() if v},
            "new_profile": new_profile,
            "consent": {
                "given": True,
                "privacy_notice_version": form.privacy_notice_version,
                "text_sha256": hashlib.sha256((form.consent_text or "").encode()).hexdigest()
                if form.consent_text else None,
                "at": now.isoformat() + "Z",
                "ip": ctx.ip,
            },
        },
        last_activity_at=now,
    )
    db.add(app_row)
    try:
        db.flush()
    except IntegrityError:  # concurrent duplicate submit
        db.rollback()
        if ctx.is_publishable:
            return S.ApplicationReceipt()
        raise _err(409, "duplicate_application", "This candidate already applied to this job.")

    documents_service.store_document(
        db, agency_id=ctx.agency_id, candidate_id=cand.id, file=vfile,
        kind="cv", uploaded_via="partner_api", application_id=app_row.id,
    )
    note = f"Applied via Partner API ({ctx.key.name})"
    if not new_profile:
        note += " — matched an existing pool profile by email; profile left unchanged"
    elif pool_match:
        note += (" — this email is already in your pool; kept as a separate profile because it came "
                 "through a client key. Review and merge if it's the same person.")
    db.add(ApplicationNote(agency_id=ctx.agency_id, application_id=app_row.id, kind="system", body=note))
    db.add(Notification(
        agency_id=ctx.agency_id,
        kind=NotificationKind.application_received,
        title=f"New applicant for {role.title}"[:240],
        body=f"{cand.full_name or 'A candidate'} applied via {form.source_site or ctx.key.name}"[:2000],
        parent_kind="application",
        parent_id=app_row.id,
    ))
    webhooks.emit(
        db, ctx.agency_id, "application.created",
        {"application_id": app_row.id, "job_id": role.id, "candidate_id": cand.id,
         "stage": app_row.stage.value, "source": "partner_api", "external_ref": app_row.external_ref},
        client_id=role.client_id,
        intake_client_id=ctx.client_id,
    )
    db.commit()
    usage_service.record(db, ctx.agency_id, UsageKind.api_application_received)

    partner_jobs.dispatch_process_application(background, app_row.id)
    partner_jobs.dispatch_webhooks(background, ctx.agency_id)

    if ctx.is_publishable:
        return S.ApplicationReceipt()
    out = S.ApplicationCreated(
        application_id=app_row.id, candidate_id=cand.id, job_id=role.id,
        stage=app_row.stage, processing_status="queued",
    )
    _idem_store(db, ctx, idem, body_hash, 202, out)
    return out


# ── Applications: read / write ──────────────────────────────────────────
@router.get("/applications", response_model=S.ApplicationList)
def list_applications(
    job_id: int | None = None,
    stage: ApplicationStage | None = None,
    updated_since: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
    ctx: PartnerContext = Depends(require_scope("applications:read")),
    db: Session = Depends(get_db),
):
    q = _apps_q(db, ctx)
    if job_id is not None:
        _load_job(db, ctx, job_id)
        q = q.filter(Application.role_id == job_id)
    if stage is not None:
        q = q.filter(Application.stage == stage)
    if updated_since is not None:
        q = q.filter(Application.updated_at >= updated_since.replace(tzinfo=None))
    rows = q.order_by(Application.id.desc()).offset(offset).limit(limit + 1).all()
    return S.ApplicationList(data=[_app_out(a) for a in rows[:limit]], **_page(len(rows), limit, offset))


@router.get("/jobs/{job_id}/applicants", response_model=S.ApplicantList)
def job_applicants(
    job_id: int,
    stage: str = Query(default="sourced", description="A pipeline stage, or 'all'"),
    sort: str = Query(default="fit_score", pattern="^(fit_score|applied_at)$"),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
    ctx: PartnerContext = Depends(require_scope("applications:read")),
    db: Session = Depends(get_db),
):
    """
    Ranked applicants for one job — built for a client portal list view.

    Default: everyone at `sourced` (new applicants), best fit first; unscored
    applicants (still processing) sort last. Each row carries the fit score
    with its evidence (reasons, gaps, breakdown, parse quality), matched and
    missing skills against the job, the applicant's own form answers, and the
    recruiter-screened fields the agency has left client-visible.

    A client-scoped key only ever sees applicants it submitted itself.
    Contact details need `candidates:read` (agency keys) or are the client's
    own applicants (client keys). Documents need `documents:read`.
    """
    from app.recruiter.services.visibility import to_client_view

    role = _load_job(db, ctx, job_id)
    q = _apps_q(db, ctx).filter(Application.role_id == role.id)
    if stage != "all":
        try:
            q = q.filter(Application.stage == ApplicationStage(stage))
        except ValueError:
            raise _err(422, "invalid_stage", "stage must be a pipeline stage or 'all'.")
    total = q.count()
    scored = q.filter(Application.fit_score.isnot(None)).count()
    if sort == "fit_score":
        q = q.order_by(Application.fit_score.is_(None), Application.fit_score.desc(), Application.id.desc())
    else:
        q = q.order_by(Application.created_at.desc(), Application.id.desc())
    rows = q.offset(offset).limit(limit + 1).all()

    req = [normalize_skill(x) for x in (role.required_skills or []) if x]
    pref = [normalize_skill(x) for x in (role.preferred_skills or []) if x]
    show_contact = ctx.client_id is not None or "candidates:read" in ctx.scopes
    show_docs = "documents:read" in ctx.scopes

    data: list[S.ApplicantOut] = []
    for i, a in enumerate(rows[:limit]):
        c = a.candidate
        cand_skills = [s.name for s in (c.skills if c else []) if s.name]
        have = {normalize_skill(x) for x in cand_skills}
        matched_req = [x for x in req if x in have]
        matched_pref = [x for x in pref if x in have]
        listed = set(matched_req) | set(matched_pref)
        view = to_client_view(a)
        screening = {k: v for k, v in view.items() if k not in ("application_id", "role_id", "candidate_id", "stage")}
        docs = None
        if show_docs:
            docs = [_doc_out(d) for d in documents_service.list_documents(
                db, ctx.agency_id, a.candidate_id,
                application_id=a.id if ctx.client_id is not None else None,
            )]
        m = a.match_explanation or None
        data.append(S.ApplicantOut(
            rank=offset + i + 1,
            application_id=a.id,
            candidate_id=a.candidate_id,
            name=c.full_name if c else None,
            headline=c.headline if c else None,
            location=c.location if c else None,
            years_experience=c.years_experience if c else None,
            fit_score=a.fit_score,
            match=S.MatchExplanation(**m) if m else None,
            skills=S.ApplicantSkills(
                matched_required=matched_req,
                matched_preferred=matched_pref,
                missing_required=[x for x in req if x not in have],
                other=[x for x in cand_skills if normalize_skill(x) not in listed][:15],
            ),
            stage=a.stage,
            source=a.source or "app",
            channel=(a.source_detail or {}).get("channel") or ("app" if (a.source or "app") == "app" else None),
            processing_status=a.processing_status or "none",
            applied_at=a.created_at,
            last_activity_at=a.last_activity_at,
            cover_letter=a.cover_letter,
            answers=a.form_answers or [],
            contact=S.ApplicantContact(email=c.email, phone=c.phone, linkedin_url=c.linkedin_url)
            if (show_contact and c) else None,
            documents=docs,
            screening=screening,
        ))
    return S.ApplicantList(
        job_id=role.id, stage=stage, total=total, scored=scored, data=data,
        **_page(len(rows), limit, offset),
    )


@router.get("/applications/{application_id}", response_model=S.ApplicationOut)
def get_application(
    application_id: int,
    ctx: PartnerContext = Depends(require_scope("applications:read")),
    db: Session = Depends(get_db),
):
    return _app_out(_load_app(db, ctx, application_id))


@router.get("/applications/{application_id}/status", response_model=S.ApplicationStatusOut)
def application_status(
    application_id: int,
    ctx: PartnerContext = Depends(require_scope("applications:read")),
    db: Session = Depends(get_db),
):
    """Downstream status: interviews, offers and placement (no fees, notes or compensation)."""
    a = _load_app(db, ctx, application_id)
    ivs = db.query(Interview).filter(Interview.agency_id == ctx.agency_id, Interview.application_id == a.id).all()
    offers = db.query(Offer).filter(Offer.agency_id == ctx.agency_id, Offer.application_id == a.id).all()
    pl = (
        db.query(Placement)
        .filter(
            Placement.agency_id == ctx.agency_id,
            Placement.candidate_id == a.candidate_id,
            Placement.role_id == a.role_id,
        )
        .order_by(Placement.id.desc())
        .first()
        if a.role_id else None
    )
    return S.ApplicationStatusOut(
        application_id=a.id,
        stage=a.stage,
        interviews=[
            S.InterviewOut(
                id=i.id, stage=getattr(i.stage, "value", str(i.stage)),
                interview_type=getattr(i.interview_type, "value", None),
                status=getattr(i.status, "value", str(i.status)), confirmed_time=i.confirmed_time,
                timezone=i.timezone, duration_minutes=i.duration_minutes,
            )
            for i in ivs
        ],
        offers=[
            S.OfferOut(id=o.id, status=getattr(o.status, "value", str(o.status)),
                       created_at=o.created_at, updated_at=o.updated_at)
            for o in offers
        ],
        placement=S.PlacementOut(
            id=pl.id, status=getattr(pl.status, "value", str(pl.status)), start_date=pl.start_date
        ) if pl else None,
    )


@router.post("/applications/{application_id}/stage", response_model=S.ApplicationOut)
def move_stage(
    application_id: int,
    payload: S.StageUpdate,
    background: BackgroundTasks,
    ctx: PartnerContext = Depends(require_write_scope("applications:write")),
    db: Session = Depends(get_db),
):
    """Move a pipeline stage (non-optimistic: the response is the stored state)."""
    require_agency_key(ctx)
    a = _load_app(db, ctx, application_id)
    if ctx.is_test:
        return JSONResponse(status_code=200, content={"test_mode": True, "valid": True})
    reason = f"via Partner API ({ctx.key.name})" + (f": {payload.reason}" if payload.reason else "")
    pipeline_service.set_stage(db, a, payload.stage, reason=reason)
    db.commit()
    db.refresh(a)
    partner_jobs.dispatch_webhooks(background, ctx.agency_id)
    return _app_out(a)


@router.post("/applications/{application_id}/notes", response_model=S.NoteOut, status_code=201)
def add_note(
    application_id: int,
    payload: S.NoteCreate,
    ctx: PartnerContext = Depends(require_write_scope("applications:write")),
    db: Session = Depends(get_db),
):
    require_agency_key(ctx)
    a = _load_app(db, ctx, application_id)
    if ctx.is_test:
        return JSONResponse(status_code=200, content={"test_mode": True, "valid": True})
    note = ApplicationNote(
        agency_id=ctx.agency_id, application_id=a.id, kind="note",
        author_name=f"API: {ctx.key.name}"[:200], body=payload.body.strip(),
    )
    db.add(note)
    a.last_activity_at = datetime.utcnow()
    db.commit()
    db.refresh(note)
    return S.NoteOut(id=note.id, body=note.body, created_at=note.created_at)


@router.post("/jobs/{job_id}/pipeline", status_code=201)
def add_candidate_to_job(
    job_id: int,
    background: BackgroundTasks,
    candidate_id: int = Query(...),
    ctx: PartnerContext = Depends(require_write_scope("applications:write")),
    db: Session = Depends(get_db),
):
    """Put an existing pool candidate into a job's pipeline at `sourced`."""
    cand = _load_candidate(db, ctx, candidate_id)
    role = _load_job(db, ctx, job_id)
    if ctx.is_test:  # before any lookup — test keys never see live records
        return JSONResponse(status_code=200, content={"test_mode": True, "valid": True})
    existing = pipeline_service.find_application(db, ctx.agency_id, role.id, cand.id)
    if existing is not None:
        return JSONResponse(status_code=200, content=jsonable_encoder(_app_out(existing)))
    a = Application(
        agency_id=ctx.agency_id, candidate_id=cand.id, role_id=role.id, job_title=role.title,
        stage=ApplicationStage.sourced, source="partner_api", intake_api_key_id=ctx.key.id,
        processing_status="queued",
    )
    db.add(a)
    db.flush()
    webhooks.emit(
        db, ctx.agency_id, "application.created",
        {"application_id": a.id, "job_id": role.id, "candidate_id": cand.id, "stage": a.stage.value,
         "source": "partner_api"},
        client_id=role.client_id,
    )
    db.commit()
    db.refresh(a)
    partner_jobs.dispatch_process_application(background, a.id)
    partner_jobs.dispatch_webhooks(background, ctx.agency_id)
    return _app_out(a)


# ── Documents ───────────────────────────────────────────────────────────
@router.get("/applications/{application_id}/documents", response_model=list[S.DocumentOut])
def application_documents(
    application_id: int,
    ctx: PartnerContext = Depends(require_scope("documents:read")),
    db: Session = Depends(get_db),
):
    a = _load_app(db, ctx, application_id)
    docs = documents_service.list_documents(
        db, ctx.agency_id, a.candidate_id,
        application_id=a.id if ctx.client_id is not None else None,
    )
    return [_doc_out(d) for d in docs]


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: int,
    ctx: PartnerContext = Depends(require_scope("documents:read")),
    db: Session = Depends(get_db),
):
    """Streams the file over this authenticated connection — no shareable URL is minted."""
    doc = documents_service.get_document(db, ctx.agency_id, document_id)
    if doc is None:
        raise _err(404, "not_found", "Document not found.")
    if ctx.client_id is not None:
        a = db.get(Application, doc.application_id) if doc.application_id else None
        if a is None or a.intake_client_id != ctx.client_id:
            raise _err(404, "not_found", "Document not found.")
    try:
        content = documents_service.read_document(doc)
    except Exception:
        raise _err(404, "not_found", "Document file is no longer available.")
    return Response(
        content=content,
        media_type=doc.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": documents_service.content_disposition(doc.original_filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


# ── Candidates ──────────────────────────────────────────────────────────
def _candidate_out(db: Session, c: CandidateProfile) -> S.CandidateOut:
    docs = documents_service.list_documents(db, c.agency_id, c.id)
    return S.CandidateOut(
        id=c.id, full_name=c.full_name, email=c.email, phone=c.phone, headline=c.headline,
        location=c.location, years_experience=c.years_experience,
        skills=[s.name for s in c.skills if s.name], linkedin_url=c.linkedin_url,
        source=getattr(c.source, "value", str(c.source)), created_at=c.created_at,
        documents=[_doc_out(d) for d in docs],
    )


@router.get("/candidates/{candidate_id}", response_model=S.CandidateOut)
def get_candidate(
    candidate_id: int,
    ctx: PartnerContext = Depends(require_scope("candidates:read")),
    db: Session = Depends(get_db),
):
    return _candidate_out(db, _load_candidate(db, ctx, candidate_id))


@router.post("/candidates", response_model=S.CandidateCreated, status_code=201)
async def create_candidate(
    request: Request,
    background: BackgroundTasks,
    email: str = Form(...),
    full_name: str | None = Form(default=None),
    phone: str | None = Form(default=None),
    headline: str | None = Form(default=None),
    location: str | None = Form(default=None),
    linkedin_url: str | None = Form(default=None),
    resume: UploadFile | None = File(default=None),
    ctx: PartnerContext = Depends(require_write_scope("candidates:write")),
    db: Session = Depends(get_db),
):
    """Push a candidate (and optionally their CV) into the agency pool. Dedupes on email."""
    require_agency_key(ctx)
    try:
        form = S.ApplicantForm(
            first_name=(full_name or "-").strip()[:100] or "-", last_name="-", email=email,
            phone=phone, linkedin_url=linkedin_url, location=location,
            consent=True, privacy_notice_version="n/a",
        )
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise _err(422, "invalid_candidate", f"Invalid fields: {', '.join(fields)}")
    if headline and len(headline) > 300:
        raise _err(422, "invalid_candidate", "Invalid fields: headline")
    vfile = None
    if resume is not None:
        content = await resume.read(documents_service.MAX_DOCUMENT_BYTES + 1)
        try:
            vfile = documents_service.validate_upload(resume.filename, content)
        except documents_service.DocumentRejected as exc:
            raise _err(400, "invalid_resume", str(exc))
    idem = _idem_key(request)
    body_hash = _hash_json({"form": form.model_dump(), "headline": headline, "file": vfile.sha256 if vfile else None})
    replay = _idem_replay(db, ctx, idem, body_hash)
    if replay:
        return replay
    if ctx.is_test:
        return JSONResponse(status_code=200, content={"test_mode": True, "valid": True})

    email_l = str(form.email).lower()
    existing = (
        db.query(CandidateProfile)
        .filter(CandidateProfile.agency_id == ctx.agency_id, func.lower(CandidateProfile.email) == email_l)
        .first()
    )
    if existing is not None:
        return JSONResponse(
            status_code=200, content=jsonable_encoder(S.CandidateCreated(candidate_id=existing.id, created=False))
        )
    cand = CandidateProfile(
        agency_id=ctx.agency_id, source=CandidateSource.partner_api,
        full_name=(full_name or "").strip()[:200] or None, email=email_l, phone=form.phone,
        headline=(headline or "").strip() or None, location=form.location, linkedin_url=form.linkedin_url,
        source_file=vfile.filename if vfile else None,
    )
    db.add(cand)
    db.flush()
    if vfile is not None:
        documents_service.store_document(
            db, agency_id=ctx.agency_id, candidate_id=cand.id, file=vfile, uploaded_via="partner_api"
        )
    db.commit()
    background.add_task(partner_jobs.process_candidate, cand.id)
    out = S.CandidateCreated(candidate_id=cand.id, created=True)
    _idem_store(db, ctx, idem, body_hash, 201, out)
    return out


@router.delete("/candidates/{candidate_id}", response_model=S.ErasureOut)
def delete_candidate(
    candidate_id: int,
    background: BackgroundTasks,
    ctx: PartnerContext = Depends(require_write_scope("candidates:delete")),
    db: Session = Depends(get_db),
):
    """Erase a candidate, their applications and stored files (GDPR erasure)."""
    cand = _load_candidate(db, ctx, candidate_id)
    if ctx.is_test:
        return JSONResponse(status_code=200, content={"test_mode": True, "valid": True})
    try:
        result = erase_candidate(db, ctx.agency_id, cand)
    except documents_service.DocumentDeleteError:
        db.rollback()
        raise _err(503, "erasure_incomplete", "Stored files could not be deleted. Nothing was erased — retry.")
    from app.recruiter.partner.audit import record as audit

    audit(db, ctx.agency_id, f"api:{ctx.key.key_prefix}", "candidate.erased", target=f"candidate:{candidate_id}")
    db.commit()
    partner_jobs.dispatch_webhooks(background, ctx.agency_id)
    return S.ErasureOut(candidate_id=candidate_id, erased=True, **result)


# ── Clients ─────────────────────────────────────────────────────────────
def _client_out(c: Client) -> S.ClientOut:
    return S.ClientOut(
        id=c.id, name=c.name, industry=c.industry, website=c.website,
        external_ref=c.external_ref, created_at=c.created_at,
    )


@router.get("/clients", response_model=S.ClientList)
def list_clients(
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
    ctx: PartnerContext = Depends(require_scope("clients:read")),
    db: Session = Depends(get_db),
):
    require_agency_key(ctx)
    rows = (
        db.query(Client).filter(Client.agency_id == ctx.agency_id)
        .order_by(Client.id.desc()).offset(offset).limit(limit + 1).all()
    )
    return S.ClientList(data=[_client_out(c) for c in rows[:limit]], **_page(len(rows), limit, offset))


@router.post("/clients", response_model=S.ClientOut, status_code=201)
def create_client(
    payload: S.ClientWrite,
    ctx: PartnerContext = Depends(require_write_scope("clients:write")),
    db: Session = Depends(get_db),
):
    require_agency_key(ctx)
    if payload.external_ref and db.query(Client).filter(
        Client.agency_id == ctx.agency_id, Client.external_ref == payload.external_ref
    ).first():
        raise _err(409, "duplicate_external_ref", "A client with this external_ref exists.")
    if ctx.is_test:
        return JSONResponse(status_code=200, content={"test_mode": True, "valid": True})
    c = Client(agency_id=ctx.agency_id, **payload.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    return _client_out(c)


@router.put("/clients/by-external-ref/{external_ref}", response_model=S.ClientOut)
def upsert_client(
    external_ref: str,
    payload: S.ClientWrite,
    response: Response,
    ctx: PartnerContext = Depends(require_write_scope("clients:write")),
    db: Session = Depends(get_db),
):
    require_agency_key(ctx)
    try:
        external_ref = S._clean_ref(external_ref)
    except ValueError as exc:
        raise _err(422, "invalid_external_ref", str(exc))
    if ctx.is_test:
        return JSONResponse(status_code=200, content={"test_mode": True, "valid": True})
    c = db.query(Client).filter(Client.agency_id == ctx.agency_id, Client.external_ref == external_ref).first()
    created = c is None
    if created:
        c = Client(agency_id=ctx.agency_id, name=payload.name)
        db.add(c)
    for k, v in payload.model_dump(exclude={"external_ref"}).items():
        setattr(c, k, v)
    c.external_ref = external_ref
    db.commit()
    db.refresh(c)
    response.status_code = 201 if created else 200
    return _client_out(c)


# ── Matching ────────────────────────────────────────────────────────────
def _shortlist_out(db: Session, sl: Shortlist) -> S.ShortlistOut:
    names = {
        c.id: c.full_name
        for c in db.query(CandidateProfile).filter(
            CandidateProfile.agency_id == sl.agency_id,
            CandidateProfile.id.in_([e.candidate_id for e in sl.entries] or [0]),
        )
    }
    return S.ShortlistOut(
        job_id=sl.role_id, shortlist_id=sl.id, created_at=getattr(sl, "created_at", None),
        entries=[
            S.ShortlistEntryOut(
                rank=e.rank, candidate_id=e.candidate_id, candidate_name=names.get(e.candidate_id),
                fit_score=e.fit_score, reasons=e.reasons or [], gaps=e.gaps or [],
                breakdown=e.score_breakdown or {},
            )
            for e in sorted(sl.entries, key=lambda x: x.rank)
        ],
    )


@router.post("/jobs/{job_id}/match", response_model=S.ShortlistOut, status_code=201)
def run_match(
    job_id: int,
    background: BackgroundTasks,
    limit: int = Query(default=25, ge=1, le=100),
    ctx: PartnerContext = Depends(require_write_scope("matching:run")),
    db: Session = Depends(get_db),
):
    """Rank the agency's pool for this job. Every score carries its reasons and gaps."""
    require_agency_key(ctx)
    from app.recruiter.services.shortlist import generate_shortlist

    role = _load_job(db, ctx, job_id)
    sl = generate_shortlist(db, role, limit=limit)
    usage_service.record(db, ctx.agency_id, UsageKind.shortlist_generated)
    webhooks.emit(db, ctx.agency_id, "shortlist.completed",
                  {"job_id": role.id, "shortlist_id": sl.id}, client_id=role.client_id)
    db.commit()
    partner_jobs.dispatch_webhooks(background, ctx.agency_id)
    return _shortlist_out(db, sl)


@router.get("/jobs/{job_id}/shortlist", response_model=S.ShortlistOut)
def latest_shortlist(
    job_id: int,
    ctx: PartnerContext = Depends(require_scope("matching:run")),
    db: Session = Depends(get_db),
):
    require_agency_key(ctx)
    role = _load_job(db, ctx, job_id)
    sl = (
        db.query(Shortlist)
        .filter(Shortlist.agency_id == ctx.agency_id, Shortlist.role_id == role.id)
        .order_by(Shortlist.id.desc())
        .first()
    )
    if sl is None:
        raise _err(404, "not_found", "No shortlist yet — POST /jobs/{job_id}/match first.")
    return _shortlist_out(db, sl)


# ── Usage ───────────────────────────────────────────────────────────────
@router.get("/usage", response_model=S.UsageOut)
def usage(
    ctx: PartnerContext = Depends(require_scope("usage:read")),
    db: Session = Depends(get_db),
):
    require_agency_key(ctx)
    summ = usage_service.summary(db, ctx.agency_id)
    start = datetime.strptime(summ.month + "-01", "%Y-%m-%d")
    reqs = int(
        db.query(func.count(ApiRequestLog.id))
        .filter(ApiRequestLog.agency_id == ctx.agency_id, ApiRequestLog.created_at >= start)
        .scalar() or 0
    )
    return S.UsageOut(month=summ.month, by_kind=summ.by_kind, total=summ.total, requests_this_month=reqs)


# ── Public XML job feed (no key; owner opt-in per agency) ───────────────
def _xml_escape(v) -> str:
    from xml.sax.saxutils import escape

    s = "" if v is None else str(v)
    s = "".join(ch for ch in s if ch in "\t\n\r" or ord(ch) >= 0x20)
    return escape(s)


@router.get("/feeds/{agency_slug}/jobs.xml", include_in_schema=True)
def public_job_feed(agency_slug: str, request: Request, db: Session = Depends(get_db)):
    """
    Job-board feed (Indeed-style XML) of open, published jobs. Off by default;
    the agency owner enables it. Contains only public job fields — the same
    data a careers page shows. No API key is needed or accepted in the URL.
    """
    from app.recruiter.enums import AgencyStatus
    from app.recruiter.models import Agency
    from app.recruiter.partner import ratelimit
    from app.recruiter.partner.deps import api_enabled_for, client_ip

    ok, _, _ = ratelimit.hit(f"feed:{ratelimit.ip_bucket(client_ip(request))}", 60, 60)
    if not ok:
        raise _err(429, "rate_limited", "Too many requests.")
    if len(agency_slug) > 120:
        raise _err(404, "not_found", "Feed not found.")
    agency = db.query(Agency).filter(Agency.slug == agency_slug).first()
    if (
        agency is None or agency.status != AgencyStatus.active
        or not agency.public_job_feed_enabled or not api_enabled_for(agency)
    ):
        raise _err(404, "not_found", "Feed not found.")
    from app.recruiter.ids import encode_id

    roles = (
        db.query(Role)
        .filter(Role.agency_id == agency.id, Role.status == RoleStatus.open, Role.is_draft.is_(False))
        .order_by(Role.id.desc())
        .limit(500)
        .all()
    )
    items = []
    for r in roles:
        items.append(
            "<job>"
            f"<referencenumber>{encode_id(r.id)}</referencenumber>"
            f"<title>{_xml_escape(r.title)}</title>"
            f"<date>{_xml_escape(r.created_at.isoformat() if r.created_at else '')}</date>"
            f"<company>{_xml_escape(agency.name)}</company>"
            f"<city>{_xml_escape(r.location)}</city>"
            f"<country>{_xml_escape(r.country_code)}</country>"
            f"<jobtype>{_xml_escape(r.employment_type.value if r.employment_type else '')}</jobtype>"
            f"<description>{_xml_escape(r.description)}</description>"
            + (f"<salary>{_xml_escape(r.salary_min)}-{_xml_escape(r.salary_max)} {_xml_escape(r.budget_currency)}</salary>"
               if r.salary_min or r.salary_max else "")
            + "</job>"
        )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f"<source><publisher>{_xml_escape(agency.name)}</publisher>"
        f"<lastBuildDate>{datetime.utcnow().isoformat()}Z</lastBuildDate>"
        + "".join(items)
        + "</source>"
    )
    return Response(
        content=xml, media_type="application/xml",
        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
    )

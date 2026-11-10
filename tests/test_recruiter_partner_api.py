"""
Partner API + secure CV storage — functional and security tests.

Offline (SQLite in-memory + mock AI). Run:
    cd backend && pytest tests/test_recruiter_partner_api.py -q
"""
import hashlib
import hmac
import json
import os
import socket
import tempfile
from datetime import timedelta

os.environ["EMBEDDING_PROVIDER"] = "mock"
os.environ.setdefault(
    "UPLOAD_DIR", os.path.join(tempfile.gettempdir(), f"partner_pytest_uploads_{os.getpid()}")
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.security import create_token, hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.models import User  # noqa: E402
from app.recruiter import models as M  # noqa: E402
from app.recruiter.partner import ratelimit, webhooks  # noqa: E402

BASE = "/api/v1/recruiter"
P = f"{BASE}/partner/v1"
ORIGIN = "https://careers.talentco.example"

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=_engine)


def _override_get_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():  # overrides conftest's file-db fixture
    ratelimit.reset_all()
    yield


@pytest.fixture(scope="module")
def client():
    prev = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    if prev is not None:
        app.dependency_overrides[get_db] = prev
    else:
        app.dependency_overrides.pop(get_db, None)


CV_ADA = b"""Ada Lovelace
Senior Backend Engineer
ada@example.com
8 years of Python, FastAPI, PostgreSQL, Docker.
Skills: Python, FastAPI, PostgreSQL, Docker
"""
CV_NEW = b"""Grace Hopper
Backend Engineer
grace@example.com
6 years of Python and FastAPI and PostgreSQL. Skills: Python, FastAPI, PostgreSQL
"""


@pytest.fixture(scope="module")
def ctx(client):
    db = Session()
    admin = User(email="op@example.com", password_hash=hash_password("x" * 12), role=UserRole.admin)
    db.add(admin)
    db.commit()
    admin_h = {"Authorization": f"Bearer {create_token(str(admin.id), 'access', timedelta(minutes=30))}"}
    db.close()

    def agency(name, slug, plan="pro"):
        a = client.post(f"{BASE}/admin/agencies", headers=admin_h, json={"name": name, "slug": slug}).json()
        client.patch(f"{BASE}/admin/agencies/{a['id']}", headers=admin_h, json={"plan": plan})
        email = f"owner@{slug}.example"
        client.post(f"{BASE}/admin/recruiters", headers=admin_h, json={
            "agency_id": a["id"], "email": email, "full_name": "Owner", "password": "ownerpass123", "role": "owner"})
        tok = client.post(f"{BASE}/auth/login", json={"email": email, "password": "ownerpass123"}).json()["access_token"]
        return a["id"], {"Authorization": f"Bearer {tok}"}

    agency_id, owner_h = agency("Talent Co", "talent-co")
    rival_id, rival_h = agency("Rival", "rival")
    free_id, free_h = agency("Freebie", "freebie", plan="free")

    client_row = client.post(f"{BASE}/agencies/{agency_id}/clients", headers=owner_h, json={"name": "Acme Ltd"}).json()
    role = client.post(f"{BASE}/agencies/{agency_id}/roles", headers=owner_h, json={
        "title": "Senior Backend Engineer", "required_skills": ["Python", "FastAPI", "PostgreSQL"],
        "min_years_experience": 5}).json()
    client.post(f"{BASE}/agencies/{agency_id}/candidates/ingest", headers=owner_h,
                files=[("files", ("ada.txt", CV_ADA, "text/plain"))])

    def key(h, **body):
        r = client.post(f"{BASE}/agency/integrations/api-keys", headers=h, json={"name": "k", **body})
        assert r.status_code == 201, r.text
        return r.json()

    secret = key(owner_h)
    pub = key(owner_h, kind="publishable", allowed_origins=[ORIGIN])
    return {
        "admin_h": admin_h, "agency_id": agency_id, "owner_h": owner_h, "rival_id": rival_id, "rival_h": rival_h,
        "free_h": free_h, "client_id": client_row["id"], "role_id": role["id"], "secret": secret, "pub": pub,
        "key": key,
    }


def H(k):
    return {"Authorization": f"Bearer {k['secret']}"}


def apply(client, job_id, k, email="grace@example.com", cv=CV_NEW, fname="grace.txt", origin=ORIGIN, **extra):
    data = {"first_name": "Grace", "last_name": "Hopper", "email": email, "consent": "true",
            "privacy_notice_version": "2026-10", "source_site": "talentco careers", **extra}
    headers = {"X-ApplyForge-Key": k["secret"]}
    if origin:
        headers["Origin"] = origin
    return client.post(f"{P}/jobs/{job_id}/applications", headers=headers, data=data,
                       files={"resume": (fname, cv, "text/plain")})


# ── Secure CV storage ──────────────────────────────────────────────────
def test_ingested_cv_is_stored_and_downloadable(client, ctx):
    a, h = ctx["agency_id"], ctx["owner_h"]
    cands = client.get(f"{BASE}/agencies/{a}/candidates", headers=h).json()
    ada = next(c for c in cands if c["full_name"] == "Ada Lovelace")
    detail = client.get(f"{BASE}/agencies/{a}/candidates/{ada['id']}", headers=h).json()
    assert detail["source_file"] == "ada.txt"  # filename only, never a server path
    docs = client.get(f"{BASE}/agencies/{a}/candidates/{ada['id']}/documents", headers=h).json()
    assert len(docs) == 1 and "storage_uri" not in docs[0]
    r = client.get(f"{BASE}/agencies/{a}/candidates/{ada['id']}/documents/{docs[0]['id']}/download", headers=h)
    assert r.status_code == 200 and r.content == CV_ADA
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in r.headers["content-disposition"]


def test_cv_download_is_tenant_isolated(client, ctx):
    a, h = ctx["agency_id"], ctx["owner_h"]
    ada = next(c for c in client.get(f"{BASE}/agencies/{a}/candidates", headers=h).json())
    docs = client.get(f"{BASE}/agencies/{a}/candidates/{ada['id']}/documents", headers=h).json()
    r = client.get(f"{BASE}/agencies/{a}/candidates/{ada['id']}/documents/{docs[0]['id']}/download",
                   headers=ctx["rival_h"])
    assert r.status_code == 403


def test_path_traversal_filename_is_neutralised(client, ctx):
    a, h = ctx["agency_id"], ctx["owner_h"]
    r = client.post(f"{BASE}/agencies/{a}/candidates/ingest", headers=h,
                    files=[("files", ("../../../../etc/evil.txt", b"Eve Evil\neve@example.com\nSkills: Go", "text/plain"))])
    assert r.status_code == 201
    db = Session()
    doc = db.query(M.CandidateDocument).order_by(M.CandidateDocument.id.desc()).first()
    assert doc.original_filename == "evil.txt" and ".." not in doc.storage_uri
    db.close()


def test_upload_rejects_mismatched_magic_bytes(client, ctx):
    r = client.post(f"{BASE}/agencies/{ctx['agency_id']}/candidates/ingest", headers=ctx["owner_h"],
                    files=[("files", ("cv.pdf", b"not really a pdf", "application/pdf"))])
    assert r.status_code == 400


# ── Auth / key handling ────────────────────────────────────────────────
def test_secret_shown_once_and_never_listed(client, ctx):
    keys = client.get(f"{BASE}/agency/integrations/api-keys", headers=ctx["owner_h"]).json()
    blob = json.dumps(keys)
    assert ctx["secret"]["secret"] not in blob and "key_hash" not in blob
    db = Session()
    row = db.query(M.ApiKey).filter(M.ApiKey.key_prefix == ctx["secret"]["key_prefix"]).one()
    assert row.key_hash != ctx["secret"]["secret"] and ctx["secret"]["secret"] not in row.key_hash
    db.close()


def test_me_and_auth_failures(client, ctx):
    assert client.get(f"{P}/me", headers=H(ctx["secret"])).json()["agency_name"] == "Talent Co"
    assert client.get(f"{P}/me").status_code == 401
    assert client.get(f"{P}/me", headers={"Authorization": "Bearer afk_live_deadbeef_nope"}).status_code == 401
    forged = ctx["secret"]["key_prefix"] + "_" + "A" * 43
    assert client.get(f"{P}/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    assert client.get(f"{P}/me?api_key=x", headers=H(ctx["secret"])).status_code == 400


def test_secret_key_refused_from_browser(client, ctx):
    r = client.get(f"{P}/me", headers={**H(ctx["secret"]), "Origin": ORIGIN})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "secret_key_in_browser"


def test_free_plan_has_no_api_until_operator_enables(client, ctx):
    r = client.post(f"{BASE}/agency/integrations/api-keys", headers=ctx["free_h"], json={"name": "x"})
    assert r.status_code == 403
    rows = client.get(f"{BASE}/admin/integrations/agencies", headers=ctx["admin_h"]).json()
    free = next(r for r in rows if r["agency_name"] == "Freebie")
    assert free["api_enabled"] is False
    client.patch(f"{BASE}/admin/integrations/agencies/{free['agency_id']}", headers=ctx["admin_h"],
                 json={"partner_api_access": "enabled"})
    k = client.post(f"{BASE}/agency/integrations/api-keys", headers=ctx["free_h"], json={"name": "x"})
    assert k.status_code == 201
    assert client.get(f"{P}/me", headers=H(k.json())).status_code == 200
    # Operator kill switch
    client.patch(f"{BASE}/admin/integrations/agencies/{free['agency_id']}", headers=ctx["admin_h"],
                 json={"partner_api_access": "disabled"})
    assert client.get(f"{P}/me", headers=H(k.json())).status_code == 403


def test_revoked_key_stops_working(client, ctx):
    k = ctx["key"](ctx["owner_h"])
    assert client.get(f"{P}/me", headers=H(k)).status_code == 200
    client.post(f"{BASE}/agency/integrations/api-keys/{k['id']}/revoke", headers=ctx["owner_h"], json={})
    assert client.get(f"{P}/me", headers=H(k)).status_code == 401


def test_roll_key_issues_new_and_revokes_old(client, ctx):
    k = ctx["key"](ctx["owner_h"])
    new = client.post(f"{BASE}/agency/integrations/api-keys/{k['id']}/roll", headers=ctx["owner_h"]).json()
    assert new["secret"] != k["secret"]
    assert client.get(f"{P}/me", headers=H(k)).status_code == 401
    assert client.get(f"{P}/me", headers=H(new)).status_code == 200


def test_scope_enforced(client, ctx):
    k = ctx["key"](ctx["owner_h"], scopes=["jobs:read"])
    assert client.get(f"{P}/jobs", headers=H(k)).status_code == 200
    r = client.post(f"{P}/jobs", headers=H(k), json={"title": "X"})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "insufficient_scope"


def test_publishable_key_cannot_get_extra_scopes(client, ctx):
    r = client.post(f"{BASE}/agency/integrations/api-keys", headers=ctx["owner_h"], json={
        "name": "bad", "kind": "publishable", "allowed_origins": [ORIGIN], "scopes": ["candidates:read"]})
    assert r.status_code == 422
    r = client.post(f"{BASE}/agency/integrations/api-keys", headers=ctx["owner_h"], json={
        "name": "bad", "kind": "publishable", "allowed_origins": ["https://*.evil.com"]})
    assert r.status_code == 422
    r = client.post(f"{BASE}/agency/integrations/api-keys", headers=ctx["owner_h"], json={
        "name": "bad", "kind": "publishable"})
    assert r.status_code == 422


def test_rate_limit(client, ctx):
    k = ctx["key"](ctx["owner_h"], rate_limit_per_minute=2)
    assert client.get(f"{P}/me", headers=H(k)).status_code == 200
    assert client.get(f"{P}/me", headers=H(k)).status_code == 200
    r = client.get(f"{P}/me", headers=H(k))
    assert r.status_code == 429 and "retry-after" in r.headers


def test_owner_cannot_exceed_plan_rate_limit(client, ctx):
    r = client.post(f"{BASE}/agency/integrations/api-keys", headers=ctx["owner_h"],
                    json={"name": "fast", "rate_limit_per_minute": 9999})
    assert r.status_code == 422


def test_non_owner_cannot_manage_integrations(client, ctx):
    client.post(f"{BASE}/admin/recruiters", headers=ctx["admin_h"], json={
        "agency_id": ctx["agency_id"], "email": "rec@talent-co.example", "password": "recpass1234"})
    tok = client.post(f"{BASE}/auth/login", json={"email": "rec@talent-co.example", "password": "recpass1234"}).json()["access_token"]
    assert client.get(f"{BASE}/agency/integrations/api-keys",
                      headers={"Authorization": f"Bearer {tok}"}).status_code == 403


# ── Jobs ────────────────────────────────────────────────────────────────
def test_job_create_upsert_and_tenant_isolation(client, ctx):
    h = H(ctx["secret"])
    r = client.put(f"{P}/jobs/by-external-ref/REQ-100", headers=h, json={
        "title": "Data Engineer", "required_skills": ["Python", "SQL"], "location": "London",
        "country_code": "gb", "currency": "gbp", "salary_min": 60000, "salary_max": 80000})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["external_ref"] == "REQ-100" and job["country_code"] == "GB" and job["source"] == "partner_api"
    assert "budget_min" not in job and "notes" not in job and "embedding" not in job
    r2 = client.put(f"{P}/jobs/by-external-ref/REQ-100", headers=h, json={"title": "Senior Data Engineer"})
    assert r2.status_code == 200 and r2.json()["id"] == job["id"] and r2.json()["title"] == "Senior Data Engineer"
    assert client.post(f"{P}/jobs", headers=h, json={"title": "Dup", "external_ref": "REQ-100"}).status_code == 409
    got = client.get(f"{P}/jobs/{job['id']}", headers=h).json()
    assert set(got["pipeline"]) >= {"sourced", "placed"}
    # Another agency's key can't see it
    rival_key = ctx["key"](ctx["rival_h"])
    assert client.get(f"{P}/jobs/{job['id']}", headers=H(rival_key)).status_code == 404
    # Raw integer ids are never accepted
    assert client.get(f"{P}/jobs/1", headers=h).status_code == 404


def test_job_validation(client, ctx):
    h = H(ctx["secret"])
    assert client.post(f"{P}/jobs", headers=h, json={"title": "X", "unknown_field": 1}).status_code == 422
    assert client.post(f"{P}/jobs", headers=h, json={"title": "X", "salary_min": 9, "salary_max": 1}).status_code == 422
    assert client.put(f"{P}/jobs/by-external-ref/bad ref!", headers=h, json={"title": "X"}).status_code == 422


def test_idempotency(client, ctx):
    h = {**H(ctx["secret"]), "Idempotency-Key": "abc-123"}
    a = client.post(f"{P}/jobs", headers=h, json={"title": "Idem Job"})
    b = client.post(f"{P}/jobs", headers=h, json={"title": "Idem Job"})
    assert a.status_code == 201 and b.json()["id"] == a.json()["id"]
    assert b.headers.get("idempotent-replayed") == "true"
    c = client.post(f"{P}/jobs", headers=h, json={"title": "Different"})
    assert c.status_code == 422


# ── Careers-site application intake ────────────────────────────────────
def test_cors_preflight_and_origin_enforcement(client, ctx):
    pre = client.options(f"{P}/jobs/x/applications", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in pre.headers
    r = client.get(f"{P}/public/jobs", headers={"X-ApplyForge-Key": ctx["pub"]["secret"], "Origin": ORIGIN})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == ORIGIN
    assert all("budget" not in json.dumps(j) and "client_id" not in j for j in r.json()["data"])
    bad = client.get(f"{P}/public/jobs", headers={"X-ApplyForge-Key": ctx["pub"]["secret"],
                                                  "Origin": "https://evil.example"})
    assert bad.status_code == 403 and "access-control-allow-origin" not in bad.headers
    assert client.get(f"{P}/public/jobs", headers={"X-ApplyForge-Key": ctx["pub"]["secret"]}).status_code == 403
    # publishable key can't read private data
    assert client.get(f"{P}/jobs", headers={"X-ApplyForge-Key": ctx["pub"]["secret"], "Origin": ORIGIN}).status_code == 403


def test_application_lands_in_pipeline_sourced_and_is_scored(client, ctx):
    r = apply(client, ctx["role_id"], ctx["pub"])
    assert r.status_code == 202 and r.json() == {"status": "received"}  # browser learns nothing more
    a, h = ctx["agency_id"], ctx["owner_h"]
    board = client.get(f"{BASE}/agencies/{a}/roles/{ctx['role_id']}/pipeline", headers=h).json()
    sourced = next(c for c in board["columns"] if c["stage"] == "sourced")["applications"]
    assert len(sourced) == 1
    app_id = sourced[0]["id"]
    out = client.get(f"{P}/applications/{app_id}", headers=H(ctx["secret"])).json()
    assert out["processing_status"] == "done" and out["fit_score"] is not None
    assert out["match"]["reasons"] and "parse_quality" in out["match"]
    db = Session()
    row = db.get(M.Application, db.query(M.Application.id).filter(M.Application.source == "partner_api").first()[0])
    assert row.source_detail["consent"]["privacy_notice_version"] == "2026-10"
    assert db.query(M.Notification).filter(M.Notification.kind == "application_received").count() >= 1
    db.close()
    docs = client.get(f"{P}/applications/{app_id}/documents", headers=H(ctx["secret"])).json()
    dl = client.get(f"{P}/documents/{docs[0]['id']}/download", headers=H(ctx["secret"]))
    assert dl.status_code == 200 and dl.content == CV_NEW


def test_duplicate_application_does_not_leak_or_duplicate(client, ctx):
    r = apply(client, ctx["role_id"], ctx["pub"])
    assert r.status_code == 202 and r.json() == {"status": "received"}
    db = Session()
    n = db.query(M.Application).filter(M.Application.role_id.isnot(None), M.Application.source == "partner_api").count()
    db.close()
    assert n == 1


def test_existing_profile_is_not_modified_by_public_form(client, ctx):
    a, h = ctx["agency_id"], ctx["owner_h"]
    job = client.post(f"{P}/jobs", headers=H(ctx["secret"]), json={"title": "Platform Engineer"}).json()
    r = apply(client, job["id"], ctx["pub"], email="ADA@example.com",
              cv=b"Mallory\nmallory@example.com\nSkills: Hacking", fname="spoof.txt")
    assert r.status_code == 202
    ada = next(c for c in client.get(f"{BASE}/agencies/{a}/candidates", headers=h).json() if c["full_name"] == "Ada Lovelace")
    detail = client.get(f"{BASE}/agencies/{a}/candidates/{ada['id']}", headers=h).json()
    assert detail["full_name"] == "Ada Lovelace"
    assert "hacking" not in [s["name"].lower() for s in detail.get("skills", [])]


def test_application_validation(client, ctx):
    assert apply(client, ctx["role_id"], ctx["pub"], email="x@example.com", consent="false").status_code == 422
    assert apply(client, ctx["role_id"], ctx["pub"], email="y@example.com", fname="cv.exe").status_code == 400
    big = b"a" * (10 * 1024 * 1024 + 10)
    assert apply(client, ctx["role_id"], ctx["pub"], email="z@example.com", cv=big).status_code == 413
    assert apply(client, ctx["role_id"], ctx["pub"], email="w@example.com",
                 first_name="<script>").status_code == 422


def test_closed_job_rejects_applications(client, ctx):
    job = client.post(f"{P}/jobs", headers=H(ctx["secret"]), json={"title": "Closing soon"}).json()
    client.post(f"{P}/jobs/{job['id']}/close", headers=H(ctx["secret"]))
    assert apply(client, job["id"], ctx["pub"], email="late@example.com").status_code == 409


def test_per_ip_limit_on_public_submissions(client, ctx, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "partner_apply_per_ip_per_hour", 1)
    job = client.post(f"{P}/jobs", headers=H(ctx["secret"]), json={"title": "Busy job"}).json()
    assert apply(client, job["id"], ctx["pub"], email="one@example.com").status_code == 202
    assert apply(client, job["id"], ctx["pub"], email="two@example.com").status_code == 429


# ── Client-scoped keys ─────────────────────────────────────────────────
def test_client_scoped_key_sees_only_its_own(client, ctx):
    ck = ctx["key"](ctx["owner_h"], client_id=ctx["client_id"])
    h = H(ck)
    job = client.post(f"{P}/jobs", headers=h, json={"title": "Acme Analyst"}).json()
    assert job["client_id"] == ctx["client_id"]
    ids = {j["id"] for j in client.get(f"{P}/jobs", headers=h).json()["data"]}
    assert ids == {job["id"]}
    assert client.get(f"{P}/jobs/{ctx['role_id']}", headers=h).status_code == 404
    # Agency adds its own pool candidate to the client's job …
    ada = next(c for c in client.get(f"{BASE}/agencies/{ctx['agency_id']}/candidates", headers=ctx["owner_h"]).json()
               if c["full_name"] == "Ada Lovelace")
    client.post(f"{P}/jobs/{job['id']}/pipeline?candidate_id={ada['id']}", headers=H(ctx["secret"]))
    # … which the client's key must NOT see (submission/consent flow protects it)
    assert client.get(f"{P}/applications?job_id={job['id']}", headers=h).json()["data"] == []
    assert client.get(f"{P}/candidates/{ada['id']}", headers=h).status_code == 403
    # Server-side intake through the client key IS visible to it
    r = client.post(f"{P}/jobs/{job['id']}/applications", headers=h,
                    data={"first_name": "Cli", "last_name": "Ent", "email": "cli@example.com", "consent": "true",
                          "privacy_notice_version": "v1"},
                    files={"resume": ("c.txt", b"Cli Ent\ncli@example.com\nSkills: Excel", "text/plain")})
    assert r.status_code == 202
    assert len(client.get(f"{P}/applications?job_id={job['id']}", headers=h).json()["data"]) == 1
    # Agency-wide powers are blocked even if the scope were requested
    bad = client.post(f"{BASE}/agency/integrations/api-keys", headers=ctx["owner_h"], json={
        "name": "x", "client_id": ctx["client_id"], "scopes": ["candidates:delete"]})
    assert bad.status_code == 422


# ── Test-mode keys ─────────────────────────────────────────────────────
def test_test_mode_key_dry_runs_and_cannot_read_live_data(client, ctx):
    tk = ctx["key"](ctx["owner_h"], environment="test")
    assert tk["secret"].startswith("afk_test_")
    db = Session()
    before = db.query(M.Role).count()
    db.close()
    r = client.post(f"{P}/jobs", headers=H(tk), json={"title": "Sandbox"})
    assert r.status_code == 200 and r.json()["test_mode"] is True
    db = Session()
    assert db.query(M.Role).count() == before
    db.close()
    assert client.get(f"{P}/jobs", headers=H(tk)).status_code == 403


# ── Pipeline, matching, erasure ────────────────────────────────────────
def test_stage_move_and_status(client, ctx):
    apps = client.get(f"{P}/applications?job_id={ctx['role_id']}", headers=H(ctx["secret"])).json()["data"]
    app_id = apps[0]["id"]
    r = client.post(f"{P}/applications/{app_id}/stage", headers=H(ctx["secret"]), json={"stage": "screening"})
    assert r.status_code == 200 and r.json()["stage"] == "screening"
    st = client.get(f"{P}/applications/{app_id}/status", headers=H(ctx["secret"])).json()
    assert st["stage"] == "screening" and "fee" not in json.dumps(st)


def test_match_returns_evidence(client, ctx):
    r = client.post(f"{P}/jobs/{ctx['role_id']}/match", headers=H(ctx["secret"]))
    assert r.status_code == 201
    top = r.json()["entries"][0]
    assert "reasons" in top and "gaps" in top and "breakdown" in top


def test_api_erasure_removes_candidate_and_files(client, ctx):
    k = ctx["key"](ctx["owner_h"], scopes=["candidates:write", "candidates:delete", "candidates:read"])
    r = client.post(f"{P}/candidates", headers=H(k), data={"email": "erase@example.com", "full_name": "Erase Me"},
                    files={"resume": ("e.txt", b"Erase Me\nerase@example.com\nSkills: Rust", "text/plain")})
    assert r.status_code == 201
    cid = r.json()["candidate_id"]
    db = Session()
    from app.recruiter.ids import decode_id
    doc = db.query(M.CandidateDocument).filter(M.CandidateDocument.candidate_id == decode_id(cid)).one()
    from app.services.storage.service import get_storage_service
    assert get_storage_service().download_bytes(doc.storage_uri)
    uri = doc.storage_uri
    db.close()
    d = client.delete(f"{P}/candidates/{cid}", headers=H(k))
    assert d.status_code == 200 and d.json()["files_removed"] == 1
    assert client.get(f"{P}/candidates/{cid}", headers=H(k)).status_code == 404
    with pytest.raises(Exception):
        get_storage_service().download_bytes(uri)


# ── Webhooks ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", [
    "http://hooks.example.com/x", "https://localhost/x", "https://127.0.0.1/x", "https://10.0.0.5/x",
    "https://169.254.169.254/latest/meta-data", "https://user:pw@hooks.example.com/x", "https://[::1]/x",
])
def test_webhook_ssrf_guard(url):
    with pytest.raises(webhooks.WebhookUrlRejected):
        webhooks.validate_url(url)


def test_webhook_delivery_is_signed_and_thin(client, ctx, monkeypatch):
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: (
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))] if host == "hooks.example.com"
        else real(host, *a, **k)))
    r = client.post(f"{BASE}/agency/integrations/webhooks", headers=ctx["owner_h"], json={
        "url": "https://hooks.example.com/af", "events": ["application.created", "application.processed"]})
    assert r.status_code == 201, r.text
    secret = r.json()["secret"]
    listed = client.get(f"{BASE}/agency/integrations/webhooks", headers=ctx["owner_h"]).json()
    assert secret not in json.dumps(listed)

    sent = []

    def fake_post(url, body, headers):
        sent.append((url, body, headers))
        return 200, None

    monkeypatch.setattr(webhooks, "_post", fake_post)
    job = client.post(f"{P}/jobs", headers=H(ctx["secret"]), json={"title": "Webhook job"}).json()
    assert apply(client, job["id"], ctx["pub"], email="hook@example.com").status_code == 202
    events = [json.loads(b)["type"] for _, b, _ in sent]
    assert "application.created" in events and "application.processed" in events
    for _, body, headers in sent:
        t, v1 = [p.split("=", 1)[1] for p in headers["ApplyForge-Signature"].split(",")]
        expect = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
        assert hmac.compare_digest(expect, v1)
        text = body.decode()
        assert "hook@example.com" not in text and "Grace" not in text  # no PII in payloads
    deliveries = client.get(f"{BASE}/agency/integrations/webhook-deliveries", headers=ctx["owner_h"]).json()
    assert any(d["status"] == "delivered" for d in deliveries)


def test_webhook_failures_back_off_then_auto_disable(client, ctx, monkeypatch):
    db = Session()
    ep = db.query(M.WebhookEndpoint).first()
    d = M.WebhookDelivery(agency_id=ep.agency_id, endpoint_id=ep.id, event_id="evt_x", event="job.created",
                          payload={"id": "evt_x"}, status="pending",
                          next_attempt_at=__import__("datetime").datetime.utcnow())
    db.add(d)
    db.commit()
    ep.consecutive_failures = webhooks.AUTO_DISABLE_AFTER - 1
    db.commit()
    webhooks.deliver_one(db, d, sender=lambda *a: (500, None))
    db.commit()
    assert d.status == "failed" and d.next_attempt_at is not None
    db.refresh(ep)
    assert ep.is_active is False and "Auto-disabled" in ep.disabled_reason
    db.close()


# ── Logs, audit, operator oversight ────────────────────────────────────
def test_request_log_has_no_secrets_or_query_strings(client, ctx):
    client.get(f"{P}/jobs?limit=5", headers=H(ctx["secret"]))
    logs = client.get(f"{BASE}/agency/integrations/request-logs", headers=ctx["owner_h"]).json()
    assert logs and all("?" not in l["path"] for l in logs)
    assert ctx["secret"]["secret"] not in json.dumps(logs)
    assert any(l["path"].endswith("/jobs") for l in logs)


def test_audit_and_operator_views(client, ctx):
    audit = client.get(f"{BASE}/agency/integrations/audit", headers=ctx["owner_h"]).json()
    assert any(a["action"] == "api_key.created" for a in audit)
    assert ctx["secret"]["secret"] not in json.dumps(audit)
    keys = client.get(f"{BASE}/admin/integrations/agencies/{ctx['agency_id']}/api-keys", headers=ctx["admin_h"]).json()
    assert keys and ctx["secret"]["secret"] not in json.dumps(keys)
    k = ctx["key"](ctx["owner_h"])
    r = client.post(f"{BASE}/admin/integrations/agencies/{ctx['agency_id']}/api-keys/{k['id']}/revoke",
                    headers=ctx["admin_h"], json={"reason": "suspicious"})
    assert r.status_code == 200 and r.json()["active"] is False
    assert client.get(f"{P}/me", headers=H(k)).status_code == 401
    # Owners can't hit operator routes
    assert client.get(f"{BASE}/admin/integrations/agencies", headers=ctx["owner_h"]).status_code in (401, 403)


def test_job_feed_is_opt_in(client, ctx):
    assert client.get(f"{P}/feeds/talent-co/jobs.xml").status_code == 404
    client.patch(f"{BASE}/agency/integrations/settings", headers=ctx["owner_h"], json={"public_job_feed_enabled": True})
    r = client.get(f"{P}/feeds/talent-co/jobs.xml")
    assert r.status_code == 200 and "<source>" in r.text
    assert "Acme Ltd" not in r.text  # client name never published
    assert "<company>Talent Co</company>" in r.text


# ── Regression tests for the independent security review ──────────────
def _client_key(ctx):
    return ctx["key"](ctx["owner_h"], client_id=ctx["client_id"],
                      scopes=["jobs:read", "jobs:write", "applications:read", "applications:create"])


def _client_apply(client, job_id, k, email, first="Cli", last="Ent"):
    return client.post(f"{P}/jobs/{job_id}/applications", headers=H(k),
                       data={"first_name": first, "last_name": last, "email": email, "consent": "true",
                             "privacy_notice_version": "v1"},
                       files={"resume": ("c.txt", f"{first} {last}\n{email}\nSkills: Excel".encode(), "text/plain")})


def test_client_key_cannot_link_to_agency_pool_profile_by_email(client, ctx):
    ck = _client_key(ctx)
    job = client.post(f"{P}/jobs", headers=H(ck), json={"title": "Acme Ops"}).json()
    r = _client_apply(client, job["id"], ck, "ada@example.com", first="Someone", last="Else")
    assert r.status_code == 202 and r.json().get("duplicate") is False
    app = client.get(f"{P}/applications/{r.json()['application_id']}", headers=H(ck)).json()
    assert app["candidate_name"] == "Someone Else"           # not the agency's "Ada Lovelace"
    db = Session()
    from app.recruiter.ids import decode_id
    a = db.get(M.Application, decode_id(r.json()["application_id"]))
    ada = db.query(M.CandidateProfile).filter(M.CandidateProfile.full_name == "Ada Lovelace").first()
    assert a.candidate_id != ada.id
    db.close()


def test_client_key_duplicate_reveals_nothing_about_agency_pipeline(client, ctx):
    ck = _client_key(ctx)
    job = client.post(f"{P}/jobs", headers=H(ck), json={"title": "Acme Ops 2"}).json()
    ada = next(c for c in client.get(f"{BASE}/agencies/{ctx['agency_id']}/candidates", headers=ctx["owner_h"]).json()
               if c["full_name"] == "Ada Lovelace")
    client.post(f"{P}/jobs/{job['id']}/pipeline?candidate_id={ada['id']}", headers=H(ctx["secret"]))
    r = _client_apply(client, job["id"], ck, "ada@example.com")
    assert r.status_code == 202 and r.json()["duplicate"] is False and r.json()["candidate_id"] != ada["id"]


def test_client_webhook_never_sees_agency_sourced_events(client, ctx, monkeypatch):
    sent = []
    monkeypatch.setattr(webhooks, "_post", lambda url, body, headers: (sent.append(json.loads(body)), (200, None))[1])
    db = Session()
    from app.recruiter.partner import crypto
    ep = M.WebhookEndpoint(agency_id=db.query(M.Agency).filter(M.Agency.slug == "talent-co").one().id,
                           client_id=__import__("app.recruiter.ids", fromlist=["decode_id"]).decode_id(ctx["client_id"]),
                           url="https://93.184.216.34/c", events=["application.created", "application.stage_changed"],
                           secret_encrypted=crypto.encrypt("whsec_x"), secret_last4="ec_x")
    db.add(ep)
    db.commit()
    ep_id = ep.id
    db.close()
    ck = _client_key(ctx)
    job = client.post(f"{P}/jobs", headers=H(ck), json={"title": "Acme Hooks"}).json()
    ada = next(c for c in client.get(f"{BASE}/agencies/{ctx['agency_id']}/candidates", headers=ctx["owner_h"]).json()
               if c["full_name"] == "Ada Lovelace")
    client.post(f"{P}/jobs/{job['id']}/pipeline?candidate_id={ada['id']}", headers=H(ctx["secret"]))
    db = Session()
    to_client = db.query(M.WebhookDelivery).filter(M.WebhookDelivery.endpoint_id == ep_id).count()
    db.close()
    assert to_client == 0
    _client_apply(client, job["id"], ck, "own-applicant@example.com")
    db = Session()
    assert db.query(M.WebhookDelivery).filter(M.WebhookDelivery.endpoint_id == ep_id).count() == 1
    db.query(M.WebhookEndpoint).filter(M.WebhookEndpoint.id == ep_id).update({"is_active": False})
    db.commit()
    db.close()


def test_operators_cannot_mint_keys_or_repoint_webhooks(client, ctx):
    r = client.post(f"{BASE}/admin/integrations/agencies/{ctx['agency_id']}/api-keys", headers=ctx["admin_h"],
                    json={"name": "op"})
    assert r.status_code in (404, 405)
    hooks = client.get(f"{BASE}/admin/integrations/agencies/{ctx['agency_id']}/webhooks", headers=ctx["admin_h"]).json()
    if hooks:
        bad = client.patch(f"{BASE}/admin/integrations/agencies/{ctx['agency_id']}/webhooks/{hooks[0]['id']}",
                           headers=ctx["admin_h"], json={"url": "https://attacker.example/x"})
        assert bad.status_code == 422
    k = ctx["key"](ctx["owner_h"])
    bad = client.patch(f"{BASE}/admin/integrations/agencies/{ctx['agency_id']}/api-keys/{k['id']}",
                       headers=ctx["admin_h"], json={"scopes": ["candidates:delete"]})
    assert bad.status_code == 422


def test_erasure_removes_notifications_and_fails_closed_on_storage_error(client, ctx, monkeypatch):
    job = client.post(f"{P}/jobs", headers=H(ctx["secret"]), json={"title": "Erasure job"}).json()
    assert apply(client, job["id"], ctx["pub"], email="gone@example.com").status_code == 202
    db = Session()
    cand = db.query(M.CandidateProfile).filter(M.CandidateProfile.email == "gone@example.com").one()
    cid, app_ids = cand.id, [a.id for a in db.query(M.Application).filter(M.Application.candidate_id == cand.id)]
    assert db.query(M.Notification).filter(M.Notification.parent_id.in_(app_ids)).count() >= 1
    db.close()
    from app.recruiter.ids import encode_id
    from app.recruiter.services import documents as docs_mod

    def boom(storage, uri):
        raise OSError("disk says no")

    monkeypatch.setattr(docs_mod, "_strict_delete", boom)
    r = client.delete(f"{BASE}/agencies/{ctx['agency_id']}/candidates/{encode_id(cid)}", headers=ctx["owner_h"])
    assert r.status_code == 503
    db = Session()
    assert db.get(M.CandidateProfile, cid) is not None   # nothing half-erased
    db.close()
    monkeypatch.undo()
    r = client.delete(f"{BASE}/agencies/{ctx['agency_id']}/candidates/{encode_id(cid)}", headers=ctx["owner_h"])
    assert r.status_code == 204
    db = Session()
    assert db.get(M.CandidateProfile, cid) is None
    assert db.query(M.Notification).filter(M.Notification.parent_kind == "application",
                                           M.Notification.parent_id.in_(app_ids)).count() == 0
    assert db.query(M.CandidateDocument).filter(M.CandidateDocument.candidate_id == cid).count() == 0
    db.close()


def test_rate_limiter_flood_does_not_reset_hourly_buckets(monkeypatch):
    monkeypatch.setattr(ratelimit, "_MAX_BUCKETS", 1000)
    ratelimit.reset_all()
    assert ratelimit.hit("ip:apply:x", 1, 3600)[0] is True
    assert ratelimit.hit("ip:apply:x", 1, 3600)[0] is False
    for i in range(5000):
        ratelimit.hit(f"feed:{i}", 60, 60)
    assert ratelimit.hit("ip:apply:x", 1, 3600)[0] is False   # still blocked
    assert len(ratelimit._buckets) <= 1000


def test_ipv6_grouped_by_64():
    assert ratelimit.ip_bucket("2001:db8::1") == ratelimit.ip_bucket("2001:db8::ffff:1234")
    assert ratelimit.ip_bucket("2001:db8:0:1::1") != ratelimit.ip_bucket("2001:db8::1")


def test_oversized_body_rejected_before_parsing(client, ctx):
    r = client.post(f"{P}/jobs", headers={**H(ctx["secret"]), "Content-Length": str(50 * 1024 * 1024),
                                           "Content-Type": "application/json"}, content=b"{}")
    assert r.status_code == 413


def test_test_key_cannot_read_via_pipeline_add(client, ctx):
    tk = ctx["key"](ctx["owner_h"], environment="test")
    ada = next(c for c in client.get(f"{BASE}/agencies/{ctx['agency_id']}/candidates", headers=ctx["owner_h"]).json()
               if c["full_name"] == "Ada Lovelace")
    r = client.post(f"{P}/jobs/{ctx['role_id']}/pipeline?candidate_id={ada['id']}", headers=H(tk))
    assert r.json() == {"test_mode": True, "valid": True}
    assert client.post(f"{P}/jobs/parse", headers=H(tk), json={"text": "x" * 40}).json()["used_llm"] is False


def test_client_key_cannot_modify_agency_created_job(client, ctx):
    ck = _client_key(ctx)
    db = Session()
    from app.recruiter.ids import decode_id, encode_id
    role = M.Role(agency_id=db.query(M.Agency).filter(M.Agency.slug == "talent-co").one().id,
                  client_id=decode_id(ctx["client_id"]), title="Agency-run role", source="app")
    db.add(role)
    db.commit()
    rid = encode_id(role.id)
    db.close()
    assert client.get(f"{P}/jobs/{rid}", headers=H(ck)).status_code == 200      # can read
    assert client.patch(f"{P}/jobs/{rid}", headers=H(ck), json={"title": "x"}).status_code == 403
    assert client.post(f"{P}/jobs/{rid}/close", headers=H(ck)).status_code == 403


def test_delivery_blocks_rebinding_to_private_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))])
    code, err = webhooks._post("https://rebind.example.com/x", b"{}", {})
    assert code is None and err == "blocked"

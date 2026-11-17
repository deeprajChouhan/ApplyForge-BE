"""
Sourced stage — LinkedIn capture lands in Sourced (with a fit score), gets
vetted + documents, then is promoted to the pool and a role pipeline.

Offline (SQLite in-memory + mock AI). Run:
    cd backend && pytest tests/test_recruiter_sourced.py -q
"""
import os
import tempfile
from datetime import timedelta

os.environ["EMBEDDING_PROVIDER"] = "mock"
os.environ.setdefault("UPLOAD_DIR", os.path.join(tempfile.gettempdir(), f"src_pytest_uploads_{os.getpid()}"))

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
from app.recruiter import enums as E  # noqa: E402
from app.recruiter import models as M  # noqa: E402
from app.recruiter.ids import decode_id, encode_id  # noqa: E402

BASE = "/api/v1/recruiter"

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


PROFILE = {
    "linkedin_url": "https://uk.linkedin.com/in/Ada-Lovelace/?trk=x",
    "full_name": "Ada Lovelace",
    "headline": "Senior Python Engineer | FastAPI, AWS",
    "location": "London, UK",
    "about": "Backend engineer with 8 years building Python services on AWS.",
    "skills": ["Python", "FastAPI", "PostgreSQL"],
    "experiences": [
        {"title": "Senior Python Engineer", "company": "Analytical Engines", "start_date": "2018-01", "end_date": None,
         "description": "Built FastAPI microservices on AWS with PostgreSQL."},
    ],
    "education": [{"school": "University of London", "degree": "BSc Mathematics"}],
    "certifications": [{"name": "AWS Solutions Architect", "issuer": "Amazon"}],
    "languages": ["English", "French"],
    "page_text": "Ada Lovelace Senior Python Engineer London Kubernetes enthusiast",
}


@pytest.fixture(scope="module")
def env(client):
    db = Session()
    admin = User(email="op@src.example", password_hash=hash_password("x" * 12), role=UserRole.admin)
    db.add(admin)
    db.commit()
    admin_h = {"Authorization": f"Bearer {create_token(str(admin.id), 'access', timedelta(minutes=30))}"}
    db.close()
    a = client.post(f"{BASE}/admin/agencies", headers=admin_h, json={"name": "Northwind", "slug": "nw-src"}).json()
    client.patch(f"{BASE}/admin/agencies/{a['id']}", headers=admin_h, json={"plan": "pro"})
    client.post(f"{BASE}/admin/recruiters", headers=admin_h, json={
        "agency_id": a["id"], "email": "nadia@nw.example", "full_name": "Nadia", "password": "seatpass123", "role": "owner"})
    tok = client.post(f"{BASE}/auth/login", json={"email": "nadia@nw.example", "password": "seatpass123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    db = Session()
    agency_id = decode_id(a["id"])
    role = M.Role(agency_id=agency_id, title="Senior Python Engineer", status=E.RoleStatus.open,
                  required_skills=["python", "fastapi", "aws"], preferred_skills=["kubernetes"],
                  min_years_experience=5, description="Python backend role")
    other = M.Agency(name="Other", slug="other-src")
    db.add_all([role, other])
    db.flush()
    other_role = M.Role(agency_id=other.id, title="Not yours", status=E.RoleStatus.open)
    db.add(other_role)
    db.commit()
    ids = {"role": encode_id(role.id), "other_role": encode_id(other_role.id)}
    db.close()
    return {"agency": a["id"], "h": h, **ids}


def u(env, path):
    return f"{BASE}/agencies/{env['agency']}/sourced{path}"


def test_score_preview_stores_nothing(client, env):
    r = client.post(u(env, "/score-preview"), headers=env["h"], json={**PROFILE, "role_id": env["role"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert 0 < body["fit_score"] <= 100
    assert body["tier"] in {"strong", "good", "low"}
    assert body["role_title"] == "Senior Python Engineer"
    assert any("required skills" in x for x in body["reasons"])
    assert body["sourced_id"] is None and body["pool_candidate_id"] is None
    assert client.get(u(env, ""), headers=env["h"]).json() == []


def test_score_preview_needs_own_role(client, env):
    assert client.post(u(env, "/score-preview"), headers=env["h"], json=PROFILE).status_code == 400
    assert client.post(u(env, "/score-preview"), headers=env["h"],
                       json={**PROFILE, "role_id": env["other_role"]}).status_code == 404


def test_save_dedups_and_does_not_touch_pool(client, env):
    r = client.post(u(env, ""), headers=env["h"], json={**PROFILE, "role_id": env["role"]})
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["created"] is True
    s = first["sourced"]
    assert s["linkedin_url"] == "https://www.linkedin.com/in/ada-lovelace"
    assert s["fit_score"] is not None and s["role_title"] == "Senior Python Engineer"

    again = client.post(u(env, ""), headers=env["h"], json={"linkedin_url": PROFILE["linkedin_url"], "full_name": None})
    assert again.json()["created"] is False
    assert again.json()["sourced"]["id"] == s["id"]
    assert again.json()["sourced"]["full_name"] == "Ada Lovelace"  # missing field never blanks

    assert client.get(f"{BASE}/agencies/{env['agency']}/candidates", headers=env["h"]).json() == []
    pv = client.post(u(env, "/score-preview"), headers=env["h"], json={**PROFILE, "role_id": env["role"]}).json()
    assert pv["sourced_id"] == s["id"] and pv["sourced_status"] == "sourced"


def test_detail_keeps_everything_scraped(client, env):
    sid = client.get(u(env, ""), headers=env["h"]).json()[0]["id"]
    d = client.get(u(env, f"/{sid}"), headers=env["h"]).json()
    assert d["education"][0]["school"] == "University of London"
    assert d["certifications"][0]["name"] == "AWS Solutions Architect"
    assert d["languages"] == ["English", "French"]
    assert d["experiences"][0]["company"] == "Analytical Engines"
    assert d["fit_reasons"] and isinstance(d["fit_breakdown"], dict)
    assert "page_text" not in d and "storage_uri" not in str(d)


def test_notes_role_change_and_archive(client, env):
    sid = client.get(u(env, ""), headers=env["h"]).json()[0]["id"]
    r = client.patch(u(env, f"/{sid}"), headers=env["h"], json={"notes": "Strong call", "email": "ada@x.example"})
    assert r.json()["notes"] == "Strong call" and r.json()["email"] == "ada@x.example"
    r = client.patch(u(env, f"/{sid}"), headers=env["h"], json={"role_id": None})
    assert r.json()["fit_score"] is None and r.json()["role_id"] is None
    r = client.patch(u(env, f"/{sid}"), headers=env["h"], json={"role_id": env["role"]})
    assert r.json()["fit_score"] is not None
    assert client.patch(u(env, f"/{sid}"), headers=env["h"], json={"role_id": env["other_role"]}).status_code == 404
    client.patch(u(env, f"/{sid}"), headers=env["h"], json={"status": "archived"})
    assert client.get(u(env, ""), headers=env["h"]).json() == []
    assert len(client.get(u(env, "?status=archived"), headers=env["h"]).json()) == 1
    client.patch(u(env, f"/{sid}"), headers=env["h"], json={"status": "sourced"})
    assert client.patch(u(env, f"/{sid}"), headers=env["h"], json={"status": "promoted"}).status_code == 400


def test_cv_upload_feeds_score_and_downloads(client, env):
    sid = client.get(u(env, ""), headers=env["h"]).json()[0]["id"]
    before = client.get(u(env, f"/{sid}"), headers=env["h"]).json()
    cv = b"Ada Lovelace\nKubernetes Docker Terraform AWS Python FastAPI\n"
    r = client.post(u(env, f"/{sid}/documents"), headers=env["h"],
                    files={"file": ("ada_cv.txt", cv, "text/plain")}, data={"kind": "cv"})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["has_text"] is True and "storage_uri" not in doc
    after = client.get(u(env, f"/{sid}"), headers=env["h"]).json()
    assert after["document_count"] == 1
    assert after["fit_score"] >= before["fit_score"]
    dl = client.get(u(env, f"/{sid}/documents/{doc['id']}/download"), headers=env["h"])
    assert dl.status_code == 200 and dl.content == cv
    bad = client.post(u(env, f"/{sid}/documents"), headers=env["h"],
                      files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert bad.status_code == 400


def test_promote_to_pool_and_role(client, env):
    sid = client.get(u(env, ""), headers=env["h"]).json()[0]["id"]
    r = client.post(u(env, f"/{sid}/promote"), headers=env["h"], json={"role_id": env["role"]})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["created"] is True and res["application_id"] and res["documents_moved"] == 1

    pool = client.get(f"{BASE}/agencies/{env['agency']}/candidates", headers=env["h"]).json()
    assert [c["id"] for c in pool] == [res["candidate_id"]]
    cand = client.get(f"{BASE}/agencies/{env['agency']}/candidates/{res['candidate_id']}", headers=env["h"]).json()
    assert cand["email"] == "ada@x.example"
    docs = client.get(f"{BASE}/agencies/{env['agency']}/candidates/{res['candidate_id']}/documents", headers=env["h"]).json()
    assert len(docs) == 1 and docs[0]["uploaded_via"] == "sourced"

    d = client.get(u(env, f"/{sid}"), headers=env["h"]).json()
    assert d["status"] == "promoted" and d["promoted_candidate_id"] == res["candidate_id"] and d["documents"] == []
    assert client.get(u(env, ""), headers=env["h"]).json() == []
    assert client.post(u(env, f"/{sid}/promote"), headers=env["h"], json={}).status_code == 400
    again = client.post(u(env, f"/{sid}/promote"), headers=env["h"], json={"role_id": env["role"]}).json()
    assert again["application_id"] == res["application_id"] and again["created"] is False

    pv = client.post(u(env, "/score-preview"), headers=env["h"], json={**PROFILE, "role_id": env["role"]}).json()
    assert pv["pool_candidate_id"] == res["candidate_id"] and pv["sourced_status"] == "promoted"


def test_delete_sourced(client, env):
    r = client.post(u(env, ""), headers=env["h"], json={"linkedin_url": "https://www.linkedin.com/in/grace-hopper", "full_name": "Grace"})
    sid = r.json()["sourced"]["id"]
    client.post(u(env, f"/{sid}/documents"), headers=env["h"], files={"file": ("g.txt", b"COBOL", "text/plain")})
    assert client.delete(u(env, f"/{sid}"), headers=env["h"]).status_code == 204
    assert client.get(u(env, f"/{sid}"), headers=env["h"]).status_code == 404


def test_bad_url_rejected(client, env):
    r = client.post(u(env, ""), headers=env["h"], json={"linkedin_url": "https://example.com/in/x"})
    assert r.status_code == 400

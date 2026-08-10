"""
Pytest suite for the recruiter platform (offline: SQLite + deterministic mock AI).

Covers auth + tenant isolation, ingestion + inverted matching, placement,
job-listing generation, market analytics, the tracking pipeline, the
company→next-hire advisory, and the provisioning bridge.

Run:  cd backend && pytest tests/test_recruiter_platform.py -q

Environment is configured at import time (before app modules load) so the app's
engine binds to a throwaway SQLite database with mock embeddings.
"""
import os
import tempfile
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["EMBEDDING_PROVIDER"] = "mock"
os.environ["UPLOAD_DIR"] = os.path.join(tempfile.gettempdir(), f"recruiter_pytest_uploads_{os.getpid()}")

from fastapi.testclient import TestClient  # noqa: E402
from app.core.security import create_token, hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.models import User  # noqa: E402

BASE = "/api/v1/recruiter"

# In-memory SQLite shared across threads (host-bind mounts break file-based
# SQLite's locking under the TestClient threadpool). All app DB access is routed
# here via a get_db dependency override.
_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=_engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(autouse=True)
def setup_db():
    """
    Override conftest's autouse fixture, which recreates the schema on
    ./test.db — a host-bind-mounted path where SQLite locking fails in this
    sandbox. This suite uses its own shared in-memory database (created once at
    import), so here we simply no-op.
    """
    yield


STRONG_CV = b"""Ada Lovelace
Senior Backend Engineer
ada@example.com

8 years of Python, FastAPI, PostgreSQL, Docker, Kubernetes, AWS, REST.
Skills: Python, FastAPI, PostgreSQL, Docker, Kubernetes, AWS, REST
"""
WEAK_CV = b"""Bob Draper
Graphic Designer
bob@example.com

Skills: Figma, HTML, CSS
"""


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def admin_headers(client) -> dict:
    db = TestingSessionLocal()
    try:
        admin = User(
            email="operator@example.com",
            password_hash=hash_password("operatorpass"),
            role=UserRole.admin,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        token = create_token(str(admin.id), "access", timedelta(minutes=30))
    finally:
        db.close()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def setup(client, admin_headers):
    """Provision an agency + recruiter, log in, seed pool + role. Returns context."""
    agency_id = client.post(
        f"{BASE}/admin/agencies", headers=admin_headers, json={"name": "Talent Co", "slug": "talent-co"}
    ).json()["id"]
    rival_id = client.post(
        f"{BASE}/admin/agencies", headers=admin_headers, json={"name": "Rival", "slug": "rival"}
    ).json()["id"]

    client.post(
        f"{BASE}/admin/recruiters",
        headers=admin_headers,
        json={"agency_id": agency_id, "email": "rita@talent.co", "full_name": "Rita", "password": "recruiterpass"},
    )
    token = client.post(
        f"{BASE}/auth/login", json={"email": "rita@talent.co", "password": "recruiterpass"}
    ).json()["access_token"]
    rec_h = {"Authorization": f"Bearer {token}"}

    role_id = client.post(
        f"{BASE}/agencies/{agency_id}/roles",
        headers=rec_h,
        json={
            "title": "Senior Backend Engineer",
            "required_skills": ["Python", "FastAPI", "PostgreSQL"],
            "preferred_skills": ["Docker", "Kubernetes", "AWS"],
            "min_years_experience": 5,
        },
    ).json()["id"]

    files = [
        ("files", ("ada.txt", STRONG_CV, "text/plain")),
        ("files", ("bob.txt", WEAK_CV, "text/plain")),
    ]
    client.post(f"{BASE}/agencies/{agency_id}/candidates/ingest", headers=rec_h, files=files)

    return {"agency_id": agency_id, "rival_id": rival_id, "role_id": role_id, "rec_h": rec_h}


# ── Auth & isolation ──────────────────────────────────────────────────────
def test_admin_endpoints_require_auth(client):
    assert client.get(f"{BASE}/admin/recruiters").status_code == 401


def test_login_rejects_bad_password(client, setup):
    assert client.post(
        f"{BASE}/auth/login", json={"email": "rita@talent.co", "password": "wrong"}
    ).status_code == 401


def test_me_returns_agency(client, setup):
    me = client.get(f"{BASE}/auth/me", headers=setup["rec_h"]).json()
    assert me["agency"]["id"] == setup["agency_id"]


def test_unauthenticated_is_blocked(client, setup):
    assert client.get(f"{BASE}/agencies/{setup['agency_id']}/candidates").status_code == 401


def test_cross_agency_is_forbidden(client, setup):
    assert client.get(
        f"{BASE}/agencies/{setup['rival_id']}/candidates", headers=setup["rec_h"]
    ).status_code == 403


# ── Ingestion + matching ──────────────────────────────────────────────────
def test_ingestion_populated_pool(client, setup):
    cands = client.get(f"{BASE}/agencies/{setup['agency_id']}/candidates", headers=setup["rec_h"]).json()
    assert len(cands) == 2
    assert {c["full_name"] for c in cands} == {"Ada Lovelace", "Bob Draper"}


def test_shortlist_ranks_backend_engineer_first(client, setup):
    sl = client.post(
        f"{BASE}/agencies/{setup['agency_id']}/roles/{setup['role_id']}/shortlist", headers=setup["rec_h"]
    ).json()
    top = sl["entries"][0]
    cand = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{top['candidate_id']}", headers=setup["rec_h"]
    ).json()
    assert cand["full_name"] == "Ada Lovelace"
    assert top["fit_score"] >= 60
    assert top["fit_score"] > sl["entries"][1]["fit_score"]


# ── Placement (candidate → roles) ─────────────────────────────────────────
def test_placement_ranks_role_for_candidate(client, setup):
    cands = client.get(f"{BASE}/agencies/{setup['agency_id']}/candidates", headers=setup["rec_h"]).json()
    ada = next(c for c in cands if c["full_name"] == "Ada Lovelace")
    rm = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{ada['id']}/role-matches", headers=setup["rec_h"]
    ).json()
    assert rm["matches"][0]["role_id"] == setup["role_id"]
    assert rm["matches"][0]["fit_score"] >= 60


# ── Job-listing generation ────────────────────────────────────────────────
def test_listing_is_grounded(client, setup):
    lj = client.post(
        f"{BASE}/agencies/{setup['agency_id']}/roles/{setup['role_id']}/listing", headers=setup["rec_h"]
    ).json()
    assert "Senior Backend Engineer" in lj["content_markdown"]
    assert any("Python" in r for r in lj["requirements"])
    assert lj["polished_by_llm"] is False  # mock mode


# ── Market analytics ──────────────────────────────────────────────────────
def test_market_demand_and_supply(client, setup):
    mj = client.get(f"{BASE}/agencies/{setup['agency_id']}/market", headers=setup["rec_h"]).json()
    assert mj["candidates_total"] == 2
    py = next((s for s in mj["skills"] if s["skill"] == "python"), None)
    assert py and py["supply"] >= 1 and py["shortage"] is False


# ── Tracking pipeline ─────────────────────────────────────────────────────
def test_pipeline_create_and_move(client, setup):
    cands = client.get(f"{BASE}/agencies/{setup['agency_id']}/candidates", headers=setup["rec_h"]).json()
    app_id = client.post(
        f"{BASE}/agencies/{setup['agency_id']}/applications",
        headers=setup["rec_h"],
        json={"candidate_id": cands[0]["id"], "stage": "sourced"},
    ).json()["id"]
    moved = client.patch(
        f"{BASE}/agencies/{setup['agency_id']}/applications/{app_id}/stage",
        headers=setup["rec_h"],
        json={"stage": "interview"},
    )
    assert moved.status_code == 200 and moved.json()["stage"] == "interview"


# ── Company → next-hire advisory ──────────────────────────────────────────
def test_next_hire_advisory_flags_gap(client, setup):
    agency_id, rec_h = setup["agency_id"], setup["rec_h"]
    client_id = client.post(
        f"{BASE}/agencies/{agency_id}/clients", headers=rec_h, json={"name": "Acme", "industry": "SaaS"}
    ).json()["id"]
    client.post(
        f"{BASE}/agencies/{agency_id}/roles",
        headers=rec_h,
        json={"title": "Frontend Engineer", "client_id": client_id, "required_skills": ["React"]},
    )
    aj = client.get(f"{BASE}/agencies/{agency_id}/clients/{client_id}/next-hire", headers=rec_h).json()
    assert aj["suggestions"]
    assert any(
        s in aj["suggestions"][0]["skills"] for s in ("python", "fastapi", "postgresql")
    )


# ── Provisioning bridge ───────────────────────────────────────────────────
def test_convert_requires_consent(client, setup):
    cands = client.get(f"{BASE}/agencies/{setup['agency_id']}/candidates", headers=setup["rec_h"]).json()
    ada = next(c for c in cands if c["full_name"] == "Ada Lovelace")
    resp = client.post(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{ada['id']}/convert",
        headers=setup["rec_h"],
        json={"consent": False},
    )
    assert resp.status_code == 400


def test_convert_creates_consumer_user_once(client, setup):
    agency_id, rec_h = setup["agency_id"], setup["rec_h"]
    cands = client.get(f"{BASE}/agencies/{agency_id}/candidates", headers=rec_h).json()
    ada = next(c for c in cands if c["full_name"] == "Ada Lovelace")

    conv = client.post(
        f"{BASE}/agencies/{agency_id}/candidates/{ada['id']}/convert", headers=rec_h, json={"consent": True}
    )
    assert conv.status_code == 201
    user_id = conv.json()["provisioned_user_id"]

    from app.models.models import Skill, User as U, UserProfile
    db = TestingSessionLocal()
    try:
        u = db.get(U, user_id)
        assert u and u.email == "ada@example.com"
        prof = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        assert prof and prof.full_name == "Ada Lovelace"
        skills = {s.name.lower() for s in db.query(Skill).filter(Skill.user_id == user_id).all()}
        assert "python" in skills
    finally:
        db.close()

    # One-way: re-converting is refused.
    again = client.post(
        f"{BASE}/agencies/{agency_id}/candidates/{ada['id']}/convert", headers=rec_h, json={"consent": True}
    )
    assert again.status_code == 409

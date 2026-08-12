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

    # Upgrade to pro so the gated AI-insight features (listings/market/advisory)
    # are available to the feature tests below.
    client.patch(f"{BASE}/admin/agencies/{agency_id}", headers=admin_headers, json={"plan": "pro"})

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


# ── Plans & seats (Phase 5.1) ─────────────────────────────────────────────
def test_free_plan_seats_and_gating(client, admin_headers):
    agency_id = client.post(
        f"{BASE}/admin/agencies", headers=admin_headers, json={"name": "Cap Co", "slug": "cap-co"}
    ).json()["id"]
    # Free plan → 2 seats, no gated features.
    ag = next(a for a in client.get(f"{BASE}/admin/agencies", headers=admin_headers).json() if a["id"] == agency_id)
    assert ag["plan"] == "free" and ag["seat_limit"] == 2 and ag["features"] == []

    for i in (1, 2):
        assert client.post(
            f"{BASE}/admin/recruiters", headers=admin_headers,
            json={"agency_id": agency_id, "email": f"seat{i}@cap.co", "password": "recruiterpass"},
        ).status_code == 201
    # Third seat is over the cap.
    assert client.post(
        f"{BASE}/admin/recruiters", headers=admin_headers,
        json={"agency_id": agency_id, "email": "seat3@cap.co", "password": "recruiterpass"},
    ).status_code == 409

    # Gating: free agency is blocked from market; upgrading to pro lifts it.
    tok = client.post(f"{BASE}/auth/login", json={"email": "seat1@cap.co", "password": "recruiterpass"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get(f"{BASE}/agencies/{agency_id}/market", headers=h).status_code == 403
    up = client.patch(f"{BASE}/admin/agencies/{agency_id}", headers=admin_headers, json={"plan": "pro"})
    assert up.status_code == 200 and up.json()["seat_limit"] == 10
    assert client.get(f"{BASE}/agencies/{agency_id}/market", headers=h).status_code == 200


# ── LinkedIn capture (Chrome extension → pool) ────────────────────────────
def _linkedin_payload(**overrides) -> dict:
    """A realistic-ish payload from the Chrome extension's LinkedIn scraper."""
    base = {
        "linkedin_url": "https://www.linkedin.com/in/carol-nguyen/",
        "full_name": "Carol Nguyen",
        "headline": "Senior Backend Engineer at BuildCo",
        "location": "London, United Kingdom",
        "about": "10+ years building distributed backends in Python, FastAPI, PostgreSQL.",
        "email": "carol@example.com",
        "skills": ["Python", "FastAPI", "Kubernetes", "AWS"],
        "experiences": [
            {
                "title": "Senior Backend Engineer",
                "company": "BuildCo",
                "start_date": "2022-03",
                "end_date": None,
                "description": "Owns the payments platform.",
            },
            {
                "title": "Backend Engineer",
                "company": "ShipShape",
                "start_date": "2018-01",
                "end_date": "2022-02",
                "description": "Python, PostgreSQL.",
            },
        ],
    }
    base.update(overrides)
    return base


def test_capture_linkedin_creates_candidate(client, setup):
    resp = client.post(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/capture-linkedin",
        headers=setup["rec_h"],
        json=_linkedin_payload(),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created"] is True
    assert body["full_name"] == "Carol Nguyen"
    assert body["linkedin_url"] == "https://www.linkedin.com/in/carol-nguyen"
    assert body["skill_count"] >= 4
    assert body["application_id"] is None

    detail = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{body['candidate_id']}",
        headers=setup["rec_h"],
    ).json()
    skill_names = {s["name"] for s in detail["skills"]}
    assert {"python", "fastapi"}.issubset(skill_names)


def test_capture_linkedin_dedups_on_recapture(client, setup):
    # First capture — creates.
    url = "https://uk.linkedin.com/in/derek-osei/"   # country subdomain → canonicalised
    first = client.post(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/capture-linkedin",
        headers=setup["rec_h"],
        json=_linkedin_payload(linkedin_url=url, full_name="Derek Osei",
                               email="derek@example.com", skills=["Python"]),
    ).json()
    assert first["created"] is True
    cand_id = first["candidate_id"]

    # Second capture of the SAME profile with a different URL surface form
    # (query string, casing) — must update in place, not create a new row.
    second = client.post(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/capture-linkedin",
        headers=setup["rec_h"],
        json=_linkedin_payload(
            linkedin_url="https://www.linkedin.com/in/Derek-Osei?utm=x",
            full_name="Derek Osei",
            headline="Staff Engineer at Acme",
            email="derek@example.com",
            skills=["Python", "Go", "Kubernetes"],
        ),
    ).json()
    assert second["created"] is False
    assert second["candidate_id"] == cand_id
    detail = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{cand_id}",
        headers=setup["rec_h"],
    ).json()
    assert detail["headline"] == "Staff Engineer at Acme"
    assert "go" in {s["name"] for s in detail["skills"]}


def test_capture_linkedin_rejects_non_profile_url(client, setup):
    resp = client.post(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/capture-linkedin",
        headers=setup["rec_h"],
        json=_linkedin_payload(linkedin_url="https://www.linkedin.com/jobs/view/12345/"),
    )
    assert resp.status_code == 400


def test_capture_linkedin_requires_auth(client, setup):
    resp = client.post(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/capture-linkedin",
        json=_linkedin_payload(linkedin_url="https://www.linkedin.com/in/anon/"),
    )
    assert resp.status_code == 401


def test_capture_linkedin_isolated_across_agencies(client, setup):
    # Recruiter authed for `agency_id` must NOT be able to capture into `rival_id`.
    resp = client.post(
        f"{BASE}/agencies/{setup['rival_id']}/candidates/capture-linkedin",
        headers=setup["rec_h"],
        json=_linkedin_payload(linkedin_url="https://www.linkedin.com/in/x-y-z/"),
    )
    assert resp.status_code == 403


def test_capture_linkedin_attaches_to_role_when_role_id_given(client, setup):
    payload = _linkedin_payload(
        linkedin_url="https://www.linkedin.com/in/emma-role-attach/",
        full_name="Emma RoleAttach",
        role_id=setup["role_id"],
    )
    first = client.post(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/capture-linkedin",
        headers=setup["rec_h"],
        json=payload,
    ).json()
    assert first["application_id"] is not None
    app_id = first["application_id"]

    # Idempotent: re-capturing with the same role_id reuses the same application.
    second = client.post(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/capture-linkedin",
        headers=setup["rec_h"],
        json=payload,
    ).json()
    assert second["created"] is False
    assert second["application_id"] == app_id


# ── Spec-sheet export (Phase 1 feature 2) ─────────────────────────────────
def _pdf_text(pdf_bytes: bytes) -> str:
    """Extract visible text from a rendered PDF.

    reportlab flate-compresses content streams so raw-byte grepping for
    "Ada Lovelace" would fail even for the non-anonymised export. pypdf
    is already a backend dep (used by parsing.py); use it to pull out
    the visible text and search that instead.
    """
    import io
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(p.extract_text() or "" for p in reader.pages)


def _docx_xml(docx_bytes: bytes) -> str:
    """python-docx stores runs as literal text in word/document.xml, so
    unzipping and reading that file is enough to search for content."""
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        return zf.read("word/document.xml").decode("utf-8")


@pytest.fixture(scope="module")
def owner_headers(client, admin_headers, setup):
    """
    An owner-tier seat for setup['agency_id']. Needed because branding +
    template CRUD are owner-scoped, and the default `rec_h` fixture is a
    plain recruiter seat.
    """
    client.post(
        f"{BASE}/admin/recruiters",
        headers=admin_headers,
        json={
            "agency_id": setup["agency_id"],
            "email": "owner@talent.co",
            "full_name": "Owner",
            "password": "recruiterpass",
            "role": "owner",
        },
    )
    token = client.post(
        f"{BASE}/auth/login",
        json={"email": "owner@talent.co", "password": "recruiterpass"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _get_ada_id(client, setup) -> int:
    cands = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates", headers=setup["rec_h"]
    ).json()
    return next(c for c in cands if c["full_name"] == "Ada Lovelace")["id"]


def test_spec_sheet_pdf_returns_a_pdf(client, setup):
    ada_id = _get_ada_id(client, setup)
    resp = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{ada_id}/spec-sheet.pdf",
        headers=setup["rec_h"],
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers.get("content-disposition", "").startswith("attachment;")
    assert resp.content[:5] == b"%PDF-"      # every PDF starts with this magic
    assert len(resp.content) > 500


def test_spec_sheet_docx_returns_a_docx(client, setup):
    ada_id = _get_ada_id(client, setup)
    resp = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{ada_id}/spec-sheet.docx",
        headers=setup["rec_h"],
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert resp.content[:2] == b"PK"          # docx is a zip
    assert len(resp.content) > 1000


def test_spec_sheet_pdf_full_contains_name_email(client, setup):
    """Non-anonymised export includes the candidate's real identity."""
    ada_id = _get_ada_id(client, setup)
    resp = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{ada_id}/spec-sheet.pdf?anonymise=false",
        headers=setup["rec_h"],
    )
    assert resp.status_code == 200
    text = _pdf_text(resp.content)
    assert "Ada Lovelace" in text
    assert "ada@example.com" in text


def test_spec_sheet_pdf_anonymised_strips_identity(client, setup):
    """Anonymised export replaces name with an ID and hides email/phone."""
    ada_id = _get_ada_id(client, setup)
    resp = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{ada_id}/spec-sheet.pdf?anonymise=true",
        headers=setup["rec_h"],
    )
    assert resp.status_code == 200
    text = _pdf_text(resp.content)
    assert "Ada Lovelace" not in text
    assert "ada@example.com" not in text
    assert "Candidate #" in text


def test_spec_sheet_docx_anonymised_strips_identity(client, setup):
    """DOCX anonymisation mirrors the PDF path."""
    ada_id = _get_ada_id(client, setup)
    resp = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{ada_id}/spec-sheet.docx?anonymise=true",
        headers=setup["rec_h"],
    )
    assert resp.status_code == 200
    xml = _docx_xml(resp.content)
    assert "Ada Lovelace" not in xml
    assert "ada@example.com" not in xml
    assert "Candidate #" in xml


def test_spec_sheet_pdf_with_role_id_includes_fit_panel(client, setup):
    ada_id = _get_ada_id(client, setup)
    resp = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{ada_id}"
        f"/spec-sheet.pdf?role_id={setup['role_id']}",
        headers=setup["rec_h"],
    )
    assert resp.status_code == 200
    text = _pdf_text(resp.content)
    assert "Fit against role" in text
    assert "Matched skills" in text


def test_spec_sheet_export_isolated_across_agencies(client, setup):
    ada_id = _get_ada_id(client, setup)
    resp = client.get(
        f"{BASE}/agencies/{setup['rival_id']}/candidates/{ada_id}/spec-sheet.pdf",
        headers=setup["rec_h"],
    )
    assert resp.status_code == 403


def test_spec_sheet_export_requires_auth(client, setup):
    ada_id = _get_ada_id(client, setup)
    resp = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{ada_id}/spec-sheet.pdf"
    )
    assert resp.status_code == 401


def test_agency_branding_update_reflects_in_overview(client, owner_headers):
    resp = client.patch(
        f"{BASE}/agency/branding",
        headers=owner_headers,
        json={
            "logo_url": "https://cdn.example.com/logos/talent.png",
            "primary_color": "#b91c1c",
            "footer_text": "Talent Co Ltd · Registered in England · 12345678",
        },
    )
    assert resp.status_code == 200, resp.text
    ov = client.get(f"{BASE}/agency/overview", headers=owner_headers).json()
    assert ov["logo_url"] == "https://cdn.example.com/logos/talent.png"
    assert ov["primary_color"] == "#b91c1c"
    assert ov["footer_text"].startswith("Talent Co Ltd")


def test_spec_sheet_uses_configured_footer(client, setup, owner_headers):
    """After the owner sets a footer, exports carry it. Fits after the branding
    update test since fixtures run in module order — this reads the state left
    behind by test_agency_branding_update_reflects_in_overview."""
    ada_id = _get_ada_id(client, setup)
    resp = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{ada_id}/spec-sheet.pdf",
        headers=setup["rec_h"],
    )
    assert resp.status_code == 200
    text = _pdf_text(resp.content)
    assert "Talent Co Ltd" in text


def test_spec_sheet_template_crud_and_override(client, setup, owner_headers):
    # Create a template that flips the branding + defaults to anonymised.
    created = client.post(
        f"{BASE}/agency/spec-sheet-templates",
        headers=owner_headers,
        json={
            "name": "Anonymised client submission",
            "header_text": "CONFIDENTIAL CANDIDATE",
            "footer_text": "Talent Co · Anonymised submission",
            "primary_color": "#065f46",
            "anonymise_by_default": True,
        },
    )
    assert created.status_code == 201, created.text
    tid = created.json()["id"]

    listing = client.get(f"{BASE}/agency/spec-sheet-templates", headers=owner_headers).json()
    assert any(t["id"] == tid for t in listing)

    # Export using this template — anonymise flag is derived from the template.
    ada_id = _get_ada_id(client, setup)
    resp = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{ada_id}"
        f"/spec-sheet.pdf?template_id={tid}",
        headers=setup["rec_h"],
    )
    assert resp.status_code == 200
    text = _pdf_text(resp.content)
    assert "CONFIDENTIAL CANDIDATE" in text
    assert "Ada Lovelace" not in text     # template's default anonymises

    # PATCH the template — anonymise_by_default flipped off, colour changed.
    upd = client.patch(
        f"{BASE}/agency/spec-sheet-templates/{tid}",
        headers=owner_headers,
        json={"anonymise_by_default": False, "primary_color": "#1d4ed8"},
    ).json()
    assert upd["anonymise_by_default"] is False
    assert upd["primary_color"] == "#1d4ed8"

    resp2 = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{ada_id}"
        f"/spec-sheet.pdf?template_id={tid}",
        headers=setup["rec_h"],
    )
    text2 = _pdf_text(resp2.content)
    assert "Ada Lovelace" in text2

    # DELETE — template must go and the endpoint must 404 afterwards.
    d = client.delete(f"{BASE}/agency/spec-sheet-templates/{tid}", headers=owner_headers)
    assert d.status_code == 204
    resp3 = client.get(
        f"{BASE}/agencies/{setup['agency_id']}/candidates/{ada_id}"
        f"/spec-sheet.pdf?template_id={tid}",
        headers=setup["rec_h"],
    )
    assert resp3.status_code == 404


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

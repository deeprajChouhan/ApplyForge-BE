"""
Integration + auth smoke test for the recruiter module inside the main backend.

Runs offline: SQLite + deterministic mock embeddings. Covers the full flow the
operator uses: seed an admin, admin provisions an agency and a recruiter login,
the recruiter logs in and works only within their own agency, and tenant/auth
boundaries are enforced.

Run:  cd backend && python tests/recruiter_smoke.py
"""
import os
import sys
import tempfile
from datetime import timedelta

_tmpdb = os.path.join(tempfile.gettempdir(), "recruiter_auth.db")
if os.path.exists(_tmpdb):
    os.remove(_tmpdb)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdb}"
os.environ["UPLOAD_DIR"] = os.path.join(tempfile.gettempdir(), "recruiter_auth_uploads")
os.environ["EMBEDDING_PROVIDER"] = "mock"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.core.security import create_token, hash_password  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.models import User  # noqa: E402
from app.main import app  # noqa: E402

# Create every table (consumer + recruiter) on the throwaway sqlite DB.
Base.metadata.create_all(bind=engine)
client = TestClient(app)

BASE = "/api/v1/recruiter"

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


def _seed_admin() -> str:
    db = SessionLocal()
    try:
        admin = User(
            email="operator@example.com",
            password_hash=hash_password("operatorpass"),
            role=UserRole.admin,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        return create_token(str(admin.id), "access", timedelta(minutes=30))
    finally:
        db.close()


def main() -> int:
    admin_token = _seed_admin()
    admin_h = {"Authorization": f"Bearer {admin_token}"}

    # Admin management is gated: no token → 401.
    assert client.get(f"{BASE}/admin/recruiters").status_code == 401

    # Operator creates two agencies.
    a1 = client.post(f"{BASE}/admin/agencies", headers=admin_h, json={"name": "Talent Co", "slug": "talent-co"})
    assert a1.status_code == 201, a1.text
    agency_id = a1.json()["id"]
    rival_id = client.post(
        f"{BASE}/admin/agencies", headers=admin_h, json={"name": "Rival", "slug": "rival"}
    ).json()["id"]

    # Operator provisions a recruiter login in Talent Co.
    r = client.post(
        f"{BASE}/admin/recruiters",
        headers=admin_h,
        json={
            "agency_id": agency_id,
            "email": "recruiter@talent.co",
            "full_name": "Rita Recruiter",
            "password": "recruiterpass",
        },
    )
    assert r.status_code == 201, r.text
    recruiter_id = r.json()["id"]
    assert r.json()["agency_name"] == "Talent Co"

    # Recruiter appears in the admin list with a live agency count.
    assert client.get(f"{BASE}/admin/recruiters", headers=admin_h).json()[0]["email"] == "recruiter@talent.co"
    assert client.get(f"{BASE}/admin/agencies", headers=admin_h).json()

    # Recruiter logs in.
    login = client.post(
        f"{BASE}/auth/login", json={"email": "recruiter@talent.co", "password": "recruiterpass"}
    )
    assert login.status_code == 200, login.text
    rec_token = login.json()["access_token"]
    rec_h = {"Authorization": f"Bearer {rec_token}"}

    # Wrong password rejected.
    assert client.post(f"{BASE}/auth/login", json={"email": "recruiter@talent.co", "password": "nope"}).status_code == 401

    # /me returns the recruiter and their agency.
    me = client.get(f"{BASE}/auth/me", headers=rec_h)
    assert me.status_code == 200 and me.json()["agency"]["id"] == agency_id, me.text

    # Unauthenticated access to agency-scoped data is blocked.
    assert client.get(f"{BASE}/agencies/{agency_id}/candidates").status_code == 401
    # Recruiter cannot touch another agency.
    assert client.get(f"{BASE}/agencies/{rival_id}/candidates", headers=rec_h).status_code == 403

    # Recruiter works within their own agency: role → ingest → shortlist.
    role = client.post(
        f"{BASE}/agencies/{agency_id}/roles",
        headers=rec_h,
        json={
            "title": "Senior Backend Engineer",
            "required_skills": ["Python", "FastAPI", "PostgreSQL"],
            "preferred_skills": ["Docker", "Kubernetes", "AWS"],
            "min_years_experience": 5,
        },
    )
    assert role.status_code == 201, role.text
    role_id = role.json()["id"]

    files = [
        ("files", ("ada.txt", STRONG_CV, "text/plain")),
        ("files", ("bob.txt", WEAK_CV, "text/plain")),
    ]
    assert client.post(f"{BASE}/agencies/{agency_id}/candidates/ingest", headers=rec_h, files=files).json()["ingested"] == 2

    sl = client.post(f"{BASE}/agencies/{agency_id}/roles/{role_id}/shortlist", headers=rec_h)
    assert sl.status_code == 201, sl.text
    entries = sl.json()["entries"]
    top = entries[0]
    top_cand = client.get(f"{BASE}/agencies/{agency_id}/candidates/{top['candidate_id']}", headers=rec_h).json()
    assert top_cand["full_name"] == "Ada Lovelace" and top["fit_score"] >= 60, (top_cand, top)

    # Operator deactivates the recruiter → login is refused.
    upd = client.patch(f"{BASE}/admin/recruiters/{recruiter_id}", headers=admin_h, json={"is_active": False})
    assert upd.status_code == 200 and upd.json()["is_active"] is False, upd.text
    assert client.post(
        f"{BASE}/auth/login", json={"email": "recruiter@talent.co", "password": "recruiterpass"}
    ).status_code == 403
    # And the previously issued token no longer works.
    assert client.get(f"{BASE}/auth/me", headers=rec_h).status_code == 401

    # Operator (admin token) can still act across agencies.
    assert client.get(f"{BASE}/agencies/{agency_id}/candidates", headers=admin_h).status_code == 200

    print("RECRUITER AUTH SMOKE TEST PASSED")
    print(f"  admin → agency → recruiter provisioned; recruiter scoped to agency {agency_id}")
    print(f"  top match: {top_cand['full_name']} fit={top['fit_score']}")
    print("  isolation: cross-agency 403, unauth 401, deactivation blocks login + token")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

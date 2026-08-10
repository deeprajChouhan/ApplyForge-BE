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

    # Phase 4 — clients + company→next-hire advisory.
    client_resp = client.post(
        f"{BASE}/agencies/{agency_id}/clients", headers=rec_h, json={"name": "Acme Corp", "industry": "SaaS"}
    )
    assert client_resp.status_code == 201, client_resp.text
    client_id = client_resp.json()["id"]
    # A frontend-heavy role for this client, so backend skills become a benchmark gap.
    fe_role = client.post(
        f"{BASE}/agencies/{agency_id}/roles",
        headers=rec_h,
        json={"title": "Frontend Engineer", "client_id": client_id, "required_skills": ["React", "TypeScript"]},
    )
    assert fe_role.status_code == 201, fe_role.text
    advisory = client.get(f"{BASE}/agencies/{agency_id}/clients/{client_id}/next-hire", headers=rec_h)
    assert advisory.status_code == 200, advisory.text
    aj = advisory.json()
    assert aj["roster_roles"] == 1 and aj["suggestions"], aj
    # Backend skills (python/fastapi/postgresql) are in the book but not this client → suggested gap.
    gap_skills = aj["suggestions"][0]["skills"]
    assert any(s in gap_skills for s in ("python", "fastapi", "postgresql")), aj["suggestions"]

    # Phase 4 — placement: rank open roles for the top candidate.
    rm = client.get(
        f"{BASE}/agencies/{agency_id}/candidates/{top['candidate_id']}/role-matches", headers=rec_h
    )
    assert rm.status_code == 200, rm.text
    matches = rm.json()["matches"]
    assert matches and matches[0]["role_id"] == role_id and matches[0]["fit_score"] >= 60, matches

    # Phase 3 — grounded job-listing generation.
    listing = client.post(f"{BASE}/agencies/{agency_id}/roles/{role_id}/listing", headers=rec_h)
    assert listing.status_code == 200, listing.text
    lj = listing.json()
    assert "Senior Backend Engineer" in lj["content_markdown"], lj["content_markdown"][:200]
    assert any("Python" in r for r in lj["requirements"]), lj["requirements"]
    assert lj["candidate_sample"] == 2 and lj["polished_by_llm"] is False, lj

    # Domain 2 — tracking-only pipeline: add, list, and move a candidate's stage.
    app_created = client.post(
        f"{BASE}/agencies/{agency_id}/applications",
        headers=rec_h,
        json={"candidate_id": top["candidate_id"], "role_id": role_id, "job_title": "Senior Backend Engineer", "stage": "sourced"},
    )
    assert app_created.status_code == 201, app_created.text
    app_id = app_created.json()["id"]
    listed = client.get(f"{BASE}/agencies/{agency_id}/applications", headers=rec_h)
    assert listed.status_code == 200 and any(a["id"] == app_id for a in listed.json()), listed.text
    moved = client.patch(
        f"{BASE}/agencies/{agency_id}/applications/{app_id}/stage", headers=rec_h, json={"stage": "interview"}
    )
    assert moved.status_code == 200 and moved.json()["stage"] == "interview", moved.text

    # Phase 3 — self-contained market analytics over the agency's own data.
    market = client.get(f"{BASE}/agencies/{agency_id}/market", headers=rec_h)
    assert market.status_code == 200, market.text
    mj = market.json()
    assert mj["candidates_total"] == 2 and mj["roles_total"] >= 1, mj
    # Python is demanded by the role and present in the pool → not a shortage.
    py = next((s for s in mj["skills"] if s["skill"] == "python"), None)
    assert py and py["demand"] == 1 and py["supply"] >= 1 and py["shortage"] is False, mj["skills"]
    # Funnel includes the interview-stage application moved earlier.
    interview = next((f for f in mj["pipeline_funnel"] if f["stage"] == "interview"), None)
    assert interview and interview["count"] == 1, mj["pipeline_funnel"]

    # Section 5 — provisioning bridge: convert a profile into a real consumer user.
    top_id = top["candidate_id"]
    # Consent is required.
    no_consent = client.post(
        f"{BASE}/agencies/{agency_id}/candidates/{top_id}/convert", headers=rec_h, json={"consent": False}
    )
    assert no_consent.status_code == 400, no_consent.text
    conv = client.post(
        f"{BASE}/agencies/{agency_id}/candidates/{top_id}/convert", headers=rec_h, json={"consent": True}
    )
    assert conv.status_code == 201, conv.text
    new_user_id = conv.json()["provisioned_user_id"]
    # A real consumer user now exists with the candidate's email + imported profile.
    from app.models.models import User as _User, UserProfile as _UP, Skill as _Skill
    _db = SessionLocal()
    try:
        u = _db.get(_User, new_user_id)
        assert u and u.email == "ada@example.com", u
        prof = _db.query(_UP).filter(_UP.user_id == new_user_id).first()
        assert prof and prof.full_name == "Ada Lovelace", prof
        skills = {s.name.lower() for s in _db.query(_Skill).filter(_Skill.user_id == new_user_id).all()}
        assert "python" in skills, skills
    finally:
        _db.close()
    # The profile is now marked provisioned, and re-converting is refused (one-way).
    cand_after = client.get(f"{BASE}/agencies/{agency_id}/candidates/{top_id}", headers=rec_h).json()
    assert cand_after["provisioned_user_id"] == new_user_id, cand_after
    again = client.post(
        f"{BASE}/agencies/{agency_id}/candidates/{top_id}/convert", headers=rec_h, json={"consent": True}
    )
    assert again.status_code == 409, again.text

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

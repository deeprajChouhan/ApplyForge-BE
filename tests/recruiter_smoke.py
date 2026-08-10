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

    # Phase 5.1 — plans & seats. Agency defaults to the free plan (2 seats).
    ag = client.get(f"{BASE}/admin/agencies", headers=admin_h).json()
    talent = next(a for a in ag if a["id"] == agency_id)
    assert talent["plan"] == "free" and talent["seat_limit"] == 2 and talent["seats_used"] == 1, talent
    # Fill the second seat, then the third must be rejected.
    assert client.post(
        f"{BASE}/admin/recruiters", headers=admin_h,
        json={"agency_id": agency_id, "email": "seat2@talent.co", "password": "recruiterpass"},
    ).status_code == 201
    third = client.post(
        f"{BASE}/admin/recruiters", headers=admin_h,
        json={"agency_id": agency_id, "email": "seat3@talent.co", "password": "recruiterpass"},
    )
    assert third.status_code == 409, third.text
    # Free plan gates the AI-insight features (market/listings/advisory).
    assert client.get(f"{BASE}/agencies/{agency_id}/market", headers=rec_h).status_code == 403
    # Operator upgrades to pro → gating lifts and seat cap rises.
    upgraded = client.patch(f"{BASE}/admin/agencies/{agency_id}", headers=admin_h, json={"plan": "pro"})
    assert upgraded.status_code == 200 and upgraded.json()["plan"] == "pro" and upgraded.json()["seat_limit"] == 10, upgraded.text
    assert client.get(f"{BASE}/agencies/{agency_id}/market", headers=rec_h).status_code == 200

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

    # Phase 5.2 — usage metering rolled up for the operator. By now the recruiter
    # has ingested CVs, generated a shortlist, drafted a listing, run role-matches
    # and an advisory — each should be metered.
    usage = client.get(f"{BASE}/admin/agencies/{agency_id}/usage", headers=admin_h)
    assert usage.status_code == 200, usage.text
    uk = usage.json()["by_kind"]
    assert uk["cv_ingested"] == 2, uk
    assert uk["shortlist_generated"] >= 1 and uk["listing_drafted"] >= 1, uk
    assert uk["role_match_run"] >= 1 and uk["advisory_run"] >= 1, uk
    assert usage.json()["total"] >= 6, usage.json()

    # Phase 5.3 — agency-admin tier. Operator provisions an owner; the owner
    # self-serves their team; a non-owner recruiter is blocked.
    client.post(
        f"{BASE}/admin/recruiters", headers=admin_h,
        json={"agency_id": agency_id, "email": "owner@talent.co", "full_name": "Olga Owner",
              "password": "ownerpass1", "role": "owner"},
    )
    owner_tok = client.post(
        f"{BASE}/auth/login", json={"email": "owner@talent.co", "password": "ownerpass1"}
    ).json()["access_token"]
    owner_h = {"Authorization": f"Bearer {owner_tok}"}

    ov = client.get(f"{BASE}/agency/overview", headers=owner_h)
    assert ov.status_code == 200 and ov.json()["plan"] == "pro" and ov.json()["seat_limit"] == 10, ov.text
    team_before = client.get(f"{BASE}/agency/team", headers=owner_h).json()
    assert any(m["email"] == "owner@talent.co" for m in team_before), team_before
    added = client.post(
        f"{BASE}/agency/team", headers=owner_h,
        json={"email": "hire@talent.co", "full_name": "New Hire", "password": "hirepass1"},
    )
    assert added.status_code == 201, added.text
    # Owner can deactivate a member but not themselves.
    assert client.patch(f"{BASE}/agency/team/{added.json()['id']}", headers=owner_h, json={"is_active": False}).status_code == 200
    owner_self = next(m for m in team_before if m["email"] == "owner@talent.co")["id"]
    assert client.patch(f"{BASE}/agency/team/{owner_self}", headers=owner_h, json={"is_active": False}).status_code == 400
    # A plain recruiter is not an owner → 403 on agency-admin endpoints.
    assert client.get(f"{BASE}/agency/team", headers=rec_h).status_code == 403
    # Owner sees their own agency usage.
    assert client.get(f"{BASE}/agency/usage", headers=owner_h).status_code == 200

    # Phase 5.4 — billing. Operator sets the per-agency billing model.
    setb = client.patch(f"{BASE}/admin/agencies/{agency_id}", headers=admin_h, json={"plan": "pro", "billing_model": "per_seat"})
    assert setb.status_code == 200 and setb.json()["billing_model"] == "per_seat", setb.text
    ov2 = client.get(f"{BASE}/agency/overview", headers=owner_h).json()
    assert ov2["billing_model"] == "per_seat" and ov2["billing_enabled"] is False and "subscription_status" in ov2, ov2
    # With Stripe unconfigured, checkout is refused cleanly and the webhook is disabled.
    assert client.post(f"{BASE}/agency/billing/checkout", headers=owner_h, json={"plan": "pro"}).status_code == 400
    assert client.post(f"{BASE}/billing/webhook", data=b"{}").status_code == 503

    # ── Phase 5.5 — self-serve onboarding (operator-approved + trial lock) ──
    signup = client.post(
        f"{BASE}/auth/signup",
        json={"agency_name": "Nimbus Talent", "owner_email": "lead@nimbus.io",
              "owner_full_name": "Nadia Lead", "password": "nimbuspass1"},
    )
    assert signup.status_code == 201, signup.text
    sj = signup.json()
    assert sj["pending_approval"] is True and sj["status"] == "pending", sj
    new_agency_id = sj["agency_id"]
    # Pending agency can't log in yet.
    pending_login = client.post(f"{BASE}/auth/login", json={"email": "lead@nimbus.io", "password": "nimbuspass1"})
    assert pending_login.status_code == 403, pending_login.text
    # Duplicate signup email is refused.
    assert client.post(
        f"{BASE}/auth/signup",
        json={"agency_name": "Dupe", "owner_email": "lead@nimbus.io", "password": "nimbuspass1"},
    ).status_code == 400

    # Operator approves it (5.6) → owner can now log in.
    appr = client.post(f"{BASE}/admin/agencies/{new_agency_id}/approve", headers=admin_h)
    assert appr.status_code == 200 and appr.json()["status"] == "active", appr.text
    nimbus_tok = client.post(
        f"{BASE}/auth/login", json={"email": "lead@nimbus.io", "password": "nimbuspass1"}
    ).json()["access_token"]
    nimbus_h = {"Authorization": f"Bearer {nimbus_tok}"}
    nov = client.get(f"{BASE}/agency/overview", headers=nimbus_h).json()
    assert nov["status"] == "active" and nov["trial_days_left"] is not None and nov["locked"] is False, nov

    # Invite/claim: owner invites a recruiter, recipient claims the token.
    inv = client.post(f"{BASE}/agency/invites", headers=nimbus_h, json={"email": "rec2@nimbus.io"})
    assert inv.status_code == 201 and inv.json()["invite_url"], inv.text
    token = inv.json()["invite_url"].rsplit("/", 1)[-1]
    pub = client.get(f"{BASE}/auth/invite/{token}").json()
    assert pub["valid"] is True and pub["email"] == "rec2@nimbus.io" and pub["agency_name"] == "Nimbus Talent", pub
    accepted = client.post(f"{BASE}/auth/invite/{token}/accept", json={"password": "rec2pass12", "full_name": "Reed Two"})
    assert accepted.status_code == 200 and accepted.json()["access_token"], accepted.text
    # The claimed seat can log in.
    assert client.post(f"{BASE}/auth/login", json={"email": "rec2@nimbus.io", "password": "rec2pass12"}).status_code == 200
    # Reusing the same token now fails.
    assert client.post(f"{BASE}/auth/invite/{token}/accept", json={"password": "again1234"}).status_code == 400
    # Free plan = 2 seats; owner + claimed seat fill it, so a new invite is capped.
    assert client.post(f"{BASE}/agency/invites", headers=nimbus_h, json={"email": "rec3@nimbus.io"}).status_code == 400

    # Trial lock: force the trial to have ended with no active subscription.
    from app.recruiter.models import Agency as _Agency
    _db2 = SessionLocal()
    try:
        _ag = _db2.get(_Agency, new_agency_id)
        _ag.trial_ends_at = _ag.created_at.__class__(2000, 1, 1)  # far past
        _db2.commit()
    finally:
        _db2.close()
    locked_ov = client.get(f"{BASE}/agency/overview", headers=nimbus_h).json()
    assert locked_ov["locked"] is True, locked_ov
    # A write is blocked with 402 while locked...
    assert client.post(
        f"{BASE}/agencies/{new_agency_id}/roles", headers=nimbus_h,
        json={"title": "Blocked Role", "required_skills": ["x"]},
    ).status_code == 402
    # ...but the owner can still log in and read (to go pay).
    assert client.post(f"{BASE}/auth/login", json={"email": "lead@nimbus.io", "password": "nimbuspass1"}).status_code == 200
    # Simulate a paid subscription → unlock, writes flow again.
    _db3 = SessionLocal()
    try:
        _ag = _db3.get(_Agency, new_agency_id)
        _ag.subscription_status = "active"
        _db3.commit()
    finally:
        _db3.close()
    assert client.get(f"{BASE}/agency/overview", headers=nimbus_h).json()["locked"] is False
    assert client.post(
        f"{BASE}/agencies/{new_agency_id}/roles", headers=nimbus_h,
        json={"title": "Unblocked Role", "required_skills": ["x"]},
    ).status_code == 201

    # ── Phase 5.6 — operator oversight ──
    summ = client.get(f"{BASE}/admin/billing/summary", headers=admin_h)
    assert summ.status_code == 200, summ.text
    sm = summ.json()
    assert sm["agencies_total"] >= 3 and sm["active_subscriptions"] >= 1, sm
    assert "active" in sm["by_status"] and sm["seats_used"] >= 1, sm
    # Suspend the agency → login blocked; reactivate → restored.
    susp = client.patch(f"{BASE}/admin/agencies/{new_agency_id}/status", headers=admin_h, json={"status": "suspended"})
    assert susp.status_code == 200 and susp.json()["status"] == "suspended", susp.text
    assert client.post(f"{BASE}/auth/login", json={"email": "lead@nimbus.io", "password": "nimbuspass1"}).status_code == 403
    react = client.patch(f"{BASE}/admin/agencies/{new_agency_id}/status", headers=admin_h, json={"status": "active"})
    assert react.status_code == 200 and react.json()["status"] == "active", react.text
    assert client.post(f"{BASE}/auth/login", json={"email": "lead@nimbus.io", "password": "nimbuspass1"}).status_code == 200

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

    # ── Intelligence upgrade — LLM CV parse merge (stubbed provider) ──
    # With no real LLM configured the heuristic path is used (verified implicitly
    # above: Ada matched via dictionary skills). Here we stub the LLM parse to
    # confirm the merge overlays rich fields: novel + aliased skills normalise and
    # union, and dated experiences persist.
    from app.recruiter.services import parsing as _parsing
    from app.recruiter.services.parsing import parse_cv as _parse_cv, ParsedExperience as _PE  # noqa: F401
    _orig = _parsing.llm_parse_cv
    _parsing.llm_parse_cv = lambda text: {
        "full_name": "Grace Hopper",
        "email": "grace@navy.mil",
        "seniority": "principal",
        "years_experience": 12,
        "skills": ["Python", "cobol", "k8s"],  # alias k8s→kubernetes; cobol is novel (not in dict)
        "experiences": [
            {"title": "Principal Engineer", "company": "Navy", "start_date": "2015-03", "end_date": None,
             "description": "Compilers."},
        ],
    }
    try:
        parsed = _parse_cv(b"Grace Hopper\nPrincipal Engineer\ngrace@navy.mil\n", "grace.txt")
        assert parsed.parsed_by_llm is True, parsed
        assert parsed.years_experience == 12 and parsed.seniority == "principal", parsed
        assert "kubernetes" in parsed.skills and "cobol" in parsed.skills and "python" in parsed.skills, parsed.skills
        assert len(parsed.experiences) == 1 and parsed.experiences[0].company == "Navy", parsed.experiences
        assert parsed.experiences[0].start_date is not None and parsed.experiences[0].start_date.year == 2015, parsed.experiences
    finally:
        _parsing.llm_parse_cv = _orig
    # Malformed / missing JSON → parser returns None → heuristic fallback (no crash).
    from app.recruiter.services import ai_support as _ai
    assert _ai._extract_json("not json at all") is None
    assert _ai._extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _ai._extract_json('prefix {"skills": ["x"]} suffix') == {"skills": ["x"]}

    print("RECRUITER AUTH SMOKE TEST PASSED")
    print(f"  admin → agency → recruiter provisioned; recruiter scoped to agency {agency_id}")
    print(f"  top match: {top_cand['full_name']} fit={top['fit_score']}")
    print("  isolation: cross-agency 403, unauth 401, deactivation blocks login + token")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

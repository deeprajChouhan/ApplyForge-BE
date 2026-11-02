"""User-testing fixes: unified stage automation, dedupe, interview tz,
per-interviewer feedback, fit-score v2, keyword densification."""
from __future__ import annotations

from datetime import datetime

from tests.test_recruiter_os_all_phases import (  # reuse fixtures/helpers
    E, SessionLocal, _auth, _mk_agency, _mk_app, _mk_cand, _mk_recruiter, _mk_role, client,
)
from app.recruiter.enums import ApplicationStage
from app.recruiter.models import Application, CandidateSkill


def _stage(db, app_row):
    db.expire_all()
    return db.get(Application, app_row.id).stage.value


def test_actions_auto_advance_stage():
    db = SessionLocal()
    ag = _mk_agency(db, "PU1"); rec = _mk_recruiter(db, ag, "pu1@x.com")
    role = _mk_role(db, ag); cand = _mk_cand(db, ag); app_row = _mk_app(db, ag, cand, role, stage=ApplicationStage.sourced)
    base = f"/api/v1/recruiter/agencies/{E(ag.id)}"
    h = _auth(rec)
    assert _stage(db, app_row) == "sourced"

    r = client.patch(f"{base}/applications/{E(app_row.id)}/screening", headers=h,
                     json={"recruiter_summary": "Solid", "screening_outcome": "suitable",
                           "mark_completed": True})
    assert r.status_code == 200, r.text
    assert _stage(db, app_row) == "screening"

    r = client.post(f"{base}/submissions", headers=h,
                    json={"application_id": E(app_row.id), "mark_submitted": True})
    assert r.status_code == 201, r.text
    assert _stage(db, app_row) == "submitted"

    r = client.post(f"{base}/interviews", headers=h, json={
        "application_id": E(app_row.id), "stage": "first",
        "confirmed_time": "2026-11-05T14:00:00+04:00", "timezone": "Asia/Dubai",
        "interviewers": [{"name": "Ann", "email": "ann@c.com", "timezone": "Europe/London"}],
    })
    assert r.status_code == 201, r.text
    iv = r.json()
    # Stored as UTC, serialised with Z so browsers localise it.
    assert iv["confirmed_time"] == "2026-11-05T10:00:00Z"
    assert iv["timezone"] == "Asia/Dubai"
    assert _stage(db, app_row) == "interview"

    # Interviews list for this application is visible (Interviews page)
    r = client.get(f"{base}/interviews", headers=h, params={"application_id": E(app_row.id)})
    assert len(r.json()) == 1

    # Second round never drags stage backwards once at offer
    r = client.post(f"{base}/offers", headers=h, json={"application_id": E(app_row.id)})
    assert r.status_code == 201, r.text
    assert _stage(db, app_row) == "offer"
    client.post(f"{base}/interviews", headers=h,
                json={"application_id": E(app_row.id), "stage": "final"})
    assert _stage(db, app_row) == "offer"
    db.close()


def test_per_interviewer_feedback_kept_separately():
    db = SessionLocal()
    ag = _mk_agency(db, "PU2"); rec = _mk_recruiter(db, ag, "pu2@x.com")
    role = _mk_role(db, ag); cand = _mk_cand(db, ag); app_row = _mk_app(db, ag, cand, role, stage=ApplicationStage.sourced)
    base = f"/api/v1/recruiter/agencies/{E(ag.id)}"
    h = _auth(rec)
    iid = client.post(f"{base}/interviews", headers=h,
                      json={"application_id": E(app_row.id)}).json()["id"]
    client.patch(f"{base}/interviews/{iid}", headers=h, json={"status": "completed"})
    for body in (
        {"interviewer_name": "Ann", "interviewer_email": "ann@c.com", "technical": 4, "decision": "progress"},
        {"interviewer_name": "Bob", "interviewer_email": "bob@c.com", "technical": 2, "decision": "reject"},
        {"interviewer_name": "Ann", "interviewer_email": "ANN@c.com", "technical": 5, "decision": "progress"},
    ):
        assert client.post(f"{base}/interviews/{iid}/feedback", headers=h, json=body).status_code == 201
    fb = client.get(f"{base}/interviews/{iid}/feedback", headers=h).json()
    by = {f["interviewer_email"]: f for f in fb}
    assert set(by) == {"ann@c.com", "bob@c.com"}
    assert by["ann@c.com"]["technical"] == 5  # updated, not duplicated
    assert by["bob@c.com"]["decision"] == "reject"
    db.close()


def test_assign_dedupes_and_client_reject():
    db = SessionLocal()
    ag = _mk_agency(db, "PU3"); rec = _mk_recruiter(db, ag, "pu3@x.com")
    role = _mk_role(db, ag); cand = _mk_cand(db, ag)
    base = f"/api/v1/recruiter/agencies/{E(ag.id)}"
    h = _auth(rec)
    r = client.post(f"{base}/roles/{E(role.id)}/pipeline/assign", headers=h,
                    json={"candidate_ids": [E(cand.id), E(cand.id)]})
    assert r.status_code == 201, r.text
    assert len(r.json()["added"]) == 1
    r = client.post(f"{base}/roles/{E(role.id)}/pipeline/assign", headers=h,
                    json={"candidate_ids": [E(cand.id)]})
    assert len(r.json()["added"]) == 0
    board = client.get(f"{base}/roles/{E(role.id)}/pipeline", headers=h).json()
    assert board["total"] == 1
    # Column order follows the pipeline
    assert [c["stage"] for c in board["columns"]][:3] == ["sourced", "screening", "submitted"]
    db.close()


def test_fit_score_normalises_case_and_aliases():
    from app.recruiter.services.matching import rank_candidates
    db = SessionLocal()
    ag = _mk_agency(db, "PU4")
    role = _mk_role(db, ag)
    role.title = "Backend Engineer"
    role.required_skills = ["Python", "PostgreSQL", "Docker"]
    role.preferred_skills = []
    role.min_years_experience = 3
    cand = _mk_cand(db, ag)
    cand.headline = "Senior Backend Engineer"
    cand.years_experience = 5
    cand.raw_cv_text = "Built services with docker and kubernetes."
    for s in ("python", "mysql"):
        db.add(CandidateSkill(candidate_id=cand.id, name=s))
    db.commit(); db.refresh(cand)
    res = rank_candidates(role, [cand])[0]
    b = res.breakdown
    # python explicit (1.0) + postgresql via mysql (0.5) + docker in CV (0.8)
    assert abs(b["required_skills"] - round(2.3 / 3 * 35, 2)) < 0.01
    assert b["preferred_skills"] == 10.0  # neutral when role lists none
    assert b["experience"] == 15.0
    assert b["title"] == 10.0
    assert res.fit_score >= 70
    db.close()


def test_keyword_densify_endpoint():
    db = SessionLocal()
    ag = _mk_agency(db, "PU5"); rec = _mk_recruiter(db, ag, "pu5@x.com")
    base = f"/api/v1/recruiter/agencies/{E(ag.id)}"
    r = client.post(f"{base}/roles/keywords/densify", headers=_auth(rec), json={
        "title": "Data Engineer",
        "description": "Build pipelines in Python and SQL on AWS. Airflow a plus.",
        "required_skills": ["python", "sql"],
    })
    assert r.status_code == 200, r.text
    out = r.json()
    skills = {s["skill"] for s in out["suggestions"]}
    assert "aws" in skills  # from JD text
    assert "python" not in skills  # never re-suggest given keywords
    assert out["is_thin"] is True
    assert out["boolean_search"].startswith("python AND sql")
    db.close()

"""
Screening auto-fill from summary, locked-screening gap filling / reopen,
"competitive" compensation readiness, and live pipeline fit scores.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi.testclient import TestClient

from app.recruiter.ids import encode_id as E

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.recruiter.api.auth_routes import issue_recruiter_tokens
from app.recruiter.enums import ApplicationStage, RecruiterSeatRole
from app.recruiter.models import (
    Agency, Application, ApplicationNote, CandidateProfile, Recruiter, Role,
    Shortlist, ShortlistEntry,
)
from app.recruiter.services.screening_extract import heuristic_extract
from app.recruiter.services.shortlist import refresh_role_fit_scores
from app.recruiter.services.submissions import compensation_confirmed

client = TestClient(app)

SUMMARY = (
    "Has ~13 years of experience in embedded development. He has worked on CAN, "
    "MQTT, Microcontrollers, Linux, etc.\n"
    "Salary: Currently on 30LPA, expecting a competitive raise.\n"
    "Notice Period: Last working day is 6th December. He has negotiated with his "
    "company for an early release by 25th October which is not confirmed yet."
)


def _setup(db, country="IN"):
    ag = Agency(name="Autofill Co", slug=f"autofill-{datetime.utcnow().timestamp()}")
    db.add(ag); db.commit(); db.refresh(ag)
    rec = Recruiter(agency_id=ag.id, email=f"r{ag.id}@x.com", full_name="Rec",
                    role=RecruiterSeatRole.owner, password_hash=hash_password("pw"), is_active=True)
    role = Role(agency_id=ag.id, title="Lead Firmware Engineer", country_code=country)
    cand = CandidateProfile(agency_id=ag.id, full_name="Amir")
    db.add_all([rec, role, cand]); db.commit()
    app_row = Application(agency_id=ag.id, candidate_id=cand.id, role_id=role.id,
                          stage=ApplicationStage.screening)
    db.add(app_row); db.commit()
    for o in (rec, role, cand, app_row):
        db.refresh(o)
    headers = {"Authorization": f"Bearer {issue_recruiter_tokens(rec.id)[0]}"}
    return ag, role, cand, app_row, headers


def test_heuristic_extract_real_summary():
    out = heuristic_extract(SUMMARY, "IN", ref=date(2026, 9, 22))
    assert out["current_compensation"]["amount"] == 3_000_000
    assert out["current_compensation"]["currency"] == "INR"
    assert out["expected_compensation"]["basis"] == "competitive"
    assert "amount" not in out["expected_compensation"]  # never invents a number
    assert out["last_working_day"] == "2026-12-06"
    assert out["availability_date"] == "2026-12-07"  # early release unconfirmed
    assert out["early_release_date"] == "2026-10-25"
    assert out["early_release_confirmed"] is False
    assert out["notice_period"]["negotiable"] is True


def test_heuristic_extract_variants():
    a = heuristic_extract("current CTC 18 LPA expecting 25% hike, immediate joiner", "IN")
    assert a["current_compensation"]["amount"] == 1_800_000
    assert a["expected_compensation"]["target"] == 2_250_000
    assert a["expected_compensation"]["estimate_source"] == "uplift"
    assert a["availability_immediate"] is True
    b = heuristic_extract("Wants £95k, 1 month notice, prefers hybrid, open to relocate", "GB")
    assert b["expected_compensation"] == {"amount": 95000, "currency": "GBP", "period": "YEAR", "basis": "fixed"}
    assert b["notice_period"]["value"] == 1 and b["notice_period"]["unit"] == "MONTH"
    assert b["preferred_work_model"] == "hybrid" and b["relocation"] == "yes"


def test_compensation_confirmed_competitive():
    assert not compensation_confirmed({"basis": "competitive", "currency": "INR"})
    assert compensation_confirmed({"basis": "competitive", "minimum": 3_300_000, "maximum": 3_900_000})
    assert compensation_confirmed({"amount": 100})


def test_save_summary_autofills_empty_fields_only():
    db = SessionLocal()
    ag, role, cand, app_row, h = _setup(db)
    base = f"/api/v1/recruiter/agencies/{E(ag.id)}/applications/{E(app_row.id)}"
    r = client.patch(f"{base}/screening", headers=h, json={
        "recruiter_summary": SUMMARY,
        "preferred_work_model": "onsite",  # recruiter-entered: must win
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_compensation"]["amount"] == 3_000_000
    assert body["expected_compensation"]["basis"] == "competitive"
    assert body["availability_date"] == "2026-12-07" or body["availability_date"].endswith("-12-07")
    assert body["preferred_work_model"] == "onsite"
    notes = db.query(ApplicationNote).filter(ApplicationNote.application_id == app_row.id).all()
    assert any("Auto-filled from summary" in n.body for n in notes)
    db.close()


def test_locked_screening_fill_gaps_conflicts_and_reopen():
    db = SessionLocal()
    ag, role, cand, app_row, h = _setup(db)
    base = f"/api/v1/recruiter/agencies/{E(ag.id)}/applications/{E(app_row.id)}"
    # Lock with a default-looking notice and nothing else.
    r = client.patch(f"{base}/screening", headers=h, json={
        "notice_period": {"value": 30, "unit": "DAY"}, "mark_completed": True,
    })
    assert r.status_code == 200 and r.json()["screening_completed_at"]

    # Overwriting a set field on a locked screening → 409
    r = client.patch(f"{base}/screening", headers=h, json={"notice_period": {"value": 60, "unit": "DAY"}})
    assert r.status_code == 409 and "notice_period" in r.json()["detail"]

    # Filling an empty field is allowed and logged
    r = client.patch(f"{base}/screening", headers=h, json={
        "expected_compensation": {"basis": "competitive", "currency": "INR", "period": "YEAR",
                                  "minimum": 3_300_000, "maximum": 3_900_000, "estimate_source": "recruiter"},
        "availability_immediate": True,
    })
    assert r.status_code == 200, r.text
    rd = client.get(f"{base}/readiness", headers=h).json()
    items = {i["key"]: i["ok"] for i in rd["items"]}
    assert items["salary_confirmed"] and items["availability_confirmed"]
    notes = db.query(ApplicationNote).filter(ApplicationNote.application_id == app_row.id).all()
    assert any("Locked screening amended" in n.body for n in notes)

    # Reopen unlocks everything
    r = client.patch(f"{base}/screening", headers=h, json={"reopen": True})
    assert r.status_code == 200 and r.json()["screening_completed_at"] is None
    r = client.patch(f"{base}/screening", headers=h, json={"notice_period": {"value": 60, "unit": "DAY"}})
    assert r.status_code == 200
    db.close()


def test_autofill_endpoint_preview_and_apply_on_locked():
    db = SessionLocal()
    ag, role, cand, app_row, h = _setup(db)
    app_row.recruiter_summary = SUMMARY
    app_row.notice_period = {"value": 30, "unit": "DAY", "negotiable": False}
    app_row.screening_completed_at = datetime(2026, 9, 22, 12, 16)
    db.commit()
    base = f"/api/v1/recruiter/agencies/{E(ag.id)}/applications/{E(app_row.id)}"
    r = client.post(f"{base}/screening/autofill", headers=h, json={})
    assert r.status_code == 200, r.text
    by = {p["field"]: p for p in r.json()["proposals"]}
    assert by["current_compensation"]["action"] == "fill"
    assert by["availability_date"]["action"] == "fill"
    assert by["notice_period"]["action"] == "conflict"  # never auto-overwritten
    assert r.json()["last_working_day"] == "2026-12-06"

    r = client.post(f"{base}/screening/autofill", headers=h, json={"apply": True})
    assert r.status_code == 200
    out = r.json()
    assert "notice_period" not in out["written"]
    assert out["application"]["notice_period"]["value"] == 30
    assert out["application"]["availability_date"] == "2026-12-07"

    r = client.post(f"{base}/screening/autofill", headers=h,
                    json={"apply": True, "fields": ["notice_period"], "overwrite": ["notice_period"]})
    assert r.json()["written"] == ["notice_period"]
    assert r.json()["application"]["notice_period"]["negotiable"] is True
    db.close()


def test_pipeline_fit_scores_follow_latest_shortlist():
    db = SessionLocal()
    ag, role, cand, app_row, h = _setup(db)
    app_row.fit_score = 62.0
    sl = Shortlist(agency_id=ag.id, role_id=role.id)
    db.add(sl); db.flush()
    db.add(ShortlistEntry(shortlist_id=sl.id, candidate_id=cand.id, rank=1, fit_score=70.0,
                          reasons=[], gaps=[], score_breakdown={}))
    db.commit()
    assert refresh_role_fit_scores(db, role) == 1
    db.commit(); db.refresh(app_row)
    assert app_row.fit_score == 70.0
    r = client.get(f"/api/v1/recruiter/agencies/{E(ag.id)}/roles/{E(role.id)}/pipeline", headers=h)
    assert r.status_code == 200
    cards = [a for c in r.json()["columns"] for a in c["applications"]]
    assert cards[0]["fit_score"] == 70.0
    db.close()

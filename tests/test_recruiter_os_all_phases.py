"""
Recruiter OS — Phases 2-7 smoke tests.

Covers, per phase:
  - Phase 2: consent CRUD, submission readiness, submission create → snapshot,
             AI draft endpoint returns shape.
  - Phase 3: submission feedback, SLA config + summary, candidate comparison.
  - Phase 4: interview create/patch, feedback, AI brief.
  - Phase 5: offer + negotiation, placement + auto-seeded check-ins.
  - Phase 6: task create/patch, daily brief endpoint shape.
  - Phase 7: ask-the-pool, role quality, role health, client intelligence.
  - Cross-phase: agency isolation (Agency A cannot touch Agency B's rows).

All tests use the FastAPI TestClient + SQLite + mock providers (LLM off).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.core.security import hash_password, create_recruiter_access_token
from app.recruiter.enums import ApplicationStage, RecruiterSeatRole
from app.recruiter.models import (
    Agency, Application, CandidateProfile, ClientSubmission, Recruiter, Role,
)


client = TestClient(app)


def _mk_agency(db, name="TestAg"):
    a = Agency(name=name, slug=name.lower())
    db.add(a); db.commit(); db.refresh(a); return a


def _mk_recruiter(db, agency, email):
    r = Recruiter(
        agency_id=agency.id, email=email, full_name="R",
        role=RecruiterSeatRole.owner,
        password_hash=hash_password("pw"), is_active=True,
    )
    db.add(r); db.commit(); db.refresh(r); return r


def _mk_role(db, agency, country="GB"):
    r = Role(agency_id=agency.id, title="Test Role", country_code=country,
             required_skills=["python"], min_years_experience=3)
    db.add(r); db.commit(); db.refresh(r); return r


def _mk_cand(db, agency, name="C"):
    c = CandidateProfile(agency_id=agency.id, full_name=name, years_experience=5)
    db.add(c); db.commit(); db.refresh(c); return c


def _mk_app(db, agency, cand, role, stage=ApplicationStage.screening):
    a = Application(agency_id=agency.id, candidate_id=cand.id,
                    role_id=role.id, stage=stage)
    db.add(a); db.commit(); db.refresh(a); return a


def _auth(rec):
    return {"Authorization": f"Bearer {create_recruiter_access_token(rec.id)}"}


# ── Phase 2: consent + readiness + submission ──────────────────────────
def test_phase2_consent_and_submission_flow():
    db = SessionLocal()
    try:
        ag = _mk_agency(db, "P2A"); rec = _mk_recruiter(db, ag, "p2@x.com")
        role = _mk_role(db, ag); cand = _mk_cand(db, ag); app_row = _mk_app(db, ag, cand, role)

        # Readiness at start: not ready
        r = client.get(f"/api/v1/recruiter/agencies/{ag.id}/applications/{app_row.id}/readiness",
                       headers=_auth(rec))
        assert r.status_code == 200
        assert r.json()["ready"] is False

        # Create + confirm consent
        r = client.post(f"/api/v1/recruiter/agencies/{ag.id}/consents",
                        headers=_auth(rec),
                        json={"candidate_id": cand.id, "role_id": role.id,
                              "status": "confirmed", "method": "email"})
        assert r.status_code == 201
        assert r.json()["status"] == "confirmed"

        # Fill screening fields to make it fully ready
        r = client.patch(
            f"/api/v1/recruiter/agencies/{ag.id}/applications/{app_row.id}/screening",
            headers=_auth(rec),
            json={
                "recruiter_summary": "Strong candidate.",
                "expected_compensation": {"amount": 90000, "currency": "GBP", "period": "YEAR"},
                "notice_period": {"value": 1, "unit": "MONTH", "negotiable": True},
                "availability_immediate": True,
                "mark_completed": True,
            })
        assert r.status_code == 200

        r = client.get(f"/api/v1/recruiter/agencies/{ag.id}/applications/{app_row.id}/readiness",
                       headers=_auth(rec))
        assert r.json()["ready"] is True

        # Create submission — snapshots
        r = client.post(f"/api/v1/recruiter/agencies/{ag.id}/submissions",
                        headers=_auth(rec),
                        json={"application_id": app_row.id, "mark_submitted": True})
        assert r.status_code == 201, r.text
        sub = r.json()
        assert sub["status"] == "submitted"
        assert sub["compensation_snapshot"]["amount"] == 90000
        assert sub["notice_period_snapshot"]["unit"] == "MONTH"

        # AI draft endpoint returns shape even without LLM
        r = client.post(
            f"/api/v1/recruiter/agencies/{ag.id}/submissions/{sub['id']}/ai-draft",
            headers=_auth(rec),
        )
        assert r.status_code == 200
        body = r.json()
        for k in ("client_summary", "key_strengths", "potential_gaps",
                  "unsupported_claims", "used_llm", "generated_at"):
            assert k in body
    finally:
        db.close()


# ── Phase 3: feedback + SLA + comparison ──────────────────────────────
def test_phase3_feedback_sla_and_comparison():
    db = SessionLocal()
    try:
        ag = _mk_agency(db, "P3A"); rec = _mk_recruiter(db, ag, "p3@x.com")
        role = _mk_role(db, ag); c1 = _mk_cand(db, ag, "C1"); c2 = _mk_cand(db, ag, "C2")
        # Give the client id via a Client row
        from app.recruiter.models import Client
        cl = Client(agency_id=ag.id, name="Client A")
        db.add(cl); db.commit(); db.refresh(cl)
        role.client_id = cl.id; db.commit()
        a1 = _mk_app(db, ag, c1, role); a2 = _mk_app(db, ag, c2, role)

        # SLA upsert + summary
        r = client.put(f"/api/v1/recruiter/agencies/{ag.id}/clients/{cl.id}/sla",
                       headers=_auth(rec),
                       json={"expected_feedback_hours": 24})
        assert r.status_code == 200
        assert r.json()["expected_feedback_hours"] == 24

        r = client.get(f"/api/v1/recruiter/agencies/{ag.id}/clients/{cl.id}/sla-summary",
                       headers=_auth(rec))
        assert r.status_code == 200
        assert r.json()["expected_feedback_hours"] == 24

        # Create a submission, then submit feedback on it
        r = client.post(f"/api/v1/recruiter/agencies/{ag.id}/submissions",
                        headers=_auth(rec),
                        json={"application_id": a1.id, "mark_submitted": True})
        sub_id = r.json()["id"]
        r = client.post(
            f"/api/v1/recruiter/agencies/{ag.id}/submissions/{sub_id}/feedback",
            headers=_auth(rec),
            json={"decision": "reject", "reasons": ["salary", "seniority"], "comment": "no"},
        )
        assert r.status_code == 201

        # Comparison
        r = client.post(f"/api/v1/recruiter/agencies/{ag.id}/candidate-comparison",
                        headers=_auth(rec),
                        json={"role_id": role.id, "candidate_ids": [c1.id, c2.id]})
        assert r.status_code == 200
        assert len(r.json()["rows"]) == 2
    finally:
        db.close()


# ── Phase 4: interviews ─────────────────────────────────────────────
def test_phase4_interview_flow():
    db = SessionLocal()
    try:
        ag = _mk_agency(db, "P4A"); rec = _mk_recruiter(db, ag, "p4@x.com")
        role = _mk_role(db, ag); cand = _mk_cand(db, ag); app_row = _mk_app(db, ag, cand, role)

        r = client.post(f"/api/v1/recruiter/agencies/{ag.id}/interviews",
                        headers=_auth(rec),
                        json={"application_id": app_row.id, "stage": "first",
                              "interview_type": "video"})
        assert r.status_code == 201
        iid = r.json()["id"]

        r = client.patch(f"/api/v1/recruiter/agencies/{ag.id}/interviews/{iid}",
                         headers=_auth(rec),
                         json={"status": "completed"})
        assert r.status_code == 200

        r = client.post(f"/api/v1/recruiter/agencies/{ag.id}/interviews/{iid}/feedback",
                        headers=_auth(rec),
                        json={"technical": 4, "communication": 5, "decision": "progress"})
        assert r.status_code == 201

        r = client.get(f"/api/v1/recruiter/agencies/{ag.id}/interviews/{iid}/brief",
                       headers=_auth(rec))
        assert r.status_code == 200
        for k in ("likely_topics", "suggested_questions", "used_llm"):
            assert k in r.json()
    finally:
        db.close()


# ── Phase 5: offers + placements ────────────────────────────────────
def test_phase5_offer_placement_and_checkins():
    db = SessionLocal()
    try:
        ag = _mk_agency(db, "P5A"); rec = _mk_recruiter(db, ag, "p5@x.com")
        role = _mk_role(db, ag); cand = _mk_cand(db, ag); app_row = _mk_app(db, ag, cand, role)

        r = client.post(f"/api/v1/recruiter/agencies/{ag.id}/offers",
                        headers=_auth(rec),
                        json={"application_id": app_row.id,
                              "base_compensation": {"amount": 95000, "currency": "GBP", "period": "YEAR"},
                              "offer_date": "2026-09-19", "expiry_date": "2026-10-03"})
        assert r.status_code == 201
        offer_id = r.json()["id"]

        r = client.post(f"/api/v1/recruiter/agencies/{ag.id}/offers/{offer_id}/negotiations",
                        headers=_auth(rec),
                        json={"round_label": "counter", "from_party": "candidate",
                              "compensation": {"amount": 100000, "currency": "GBP", "period": "YEAR"}})
        assert r.status_code == 201

        r = client.post(f"/api/v1/recruiter/agencies/{ag.id}/placements",
                        headers=_auth(rec),
                        json={"role_id": role.id, "candidate_id": cand.id,
                              "offer_id": offer_id, "start_date": "2026-11-01",
                              "final_compensation": {"amount": 98000, "currency": "GBP", "period": "YEAR"},
                              "fee_percent": 20, "guarantee_weeks": 12})
        assert r.status_code == 201
        placement = r.json()
        assert placement["fee_amount"] == int(98000 * 0.20)
        assert placement["guarantee_ends_at"] is not None

        # Check-ins auto-seeded
        r = client.get(
            f"/api/v1/recruiter/agencies/{ag.id}/placements/{placement['id']}/checkins",
            headers=_auth(rec),
        )
        assert r.status_code == 200
        offsets = [c["day_offset"] for c in r.json()]
        assert set(offsets) == {7, 30, 60, 90}
    finally:
        db.close()


# ── Phase 6: tasks + daily brief ────────────────────────────────────
def test_phase6_tasks_and_daily_brief():
    db = SessionLocal()
    try:
        ag = _mk_agency(db, "P6A"); rec = _mk_recruiter(db, ag, "p6@x.com")

        r = client.post(f"/api/v1/recruiter/agencies/{ag.id}/tasks",
                        headers=_auth(rec),
                        json={"title": "Chase client", "priority": "high"})
        assert r.status_code == 201
        tid = r.json()["id"]
        r = client.patch(f"/api/v1/recruiter/agencies/{ag.id}/tasks/{tid}",
                         headers=_auth(rec),
                         json={"status": "done"})
        assert r.status_code == 200
        assert r.json()["completed_at"] is not None

        r = client.get(f"/api/v1/recruiter/agencies/{ag.id}/daily-brief",
                       headers=_auth(rec))
        assert r.status_code == 200
        body = r.json()
        for k in ("urgent", "high", "medium", "opportunities", "generated_at"):
            assert k in body
    finally:
        db.close()


# ── Phase 7: intelligence ──────────────────────────────────────────
def test_phase7_ask_pool_and_role_health():
    db = SessionLocal()
    try:
        ag = _mk_agency(db, "P7A"); rec = _mk_recruiter(db, ag, "p7@x.com")
        role = _mk_role(db, ag)
        for i in range(3):
            _mk_cand(db, ag, f"C{i}")

        r = client.post(f"/api/v1/recruiter/agencies/{ag.id}/intelligence/ask-pool",
                        headers=_auth(rec),
                        json={"query": "python engineers", "limit": 5})
        assert r.status_code == 200
        assert "matches" in r.json()

        r = client.get(
            f"/api/v1/recruiter/agencies/{ag.id}/intelligence/roles/{role.id}/health",
            headers=_auth(rec))
        assert r.status_code == 200
        for k in ("sourced", "screened", "submitted", "pipeline_risk"):
            assert k in r.json()

        r = client.get(
            f"/api/v1/recruiter/agencies/{ag.id}/intelligence/roles/{role.id}/quality-check",
            headers=_auth(rec))
        assert r.status_code == 200
        assert "observations" in r.json()
    finally:
        db.close()


# ── Cross-phase: agency isolation ──────────────────────────────────
def test_cross_phase_agency_isolation():
    db = SessionLocal()
    try:
        a1 = _mk_agency(db, "IsoP2A"); r1 = _mk_recruiter(db, a1, "iso-a@x.com")
        a2 = _mk_agency(db, "IsoP2B"); _r2 = _mk_recruiter(db, a2, "iso-b@x.com")
        role_b = _mk_role(db, a2); cand_b = _mk_cand(db, a2); app_b = _mk_app(db, a2, cand_b, role_b)

        # r1 (agency A) attempting to reach anything under agency B → 403 or 404
        for path in (
            f"/api/v1/recruiter/agencies/{a2.id}/applications/{app_b.id}/readiness",
            f"/api/v1/recruiter/agencies/{a2.id}/tasks",
            f"/api/v1/recruiter/agencies/{a2.id}/daily-brief",
            f"/api/v1/recruiter/agencies/{a2.id}/intelligence/roles/{role_b.id}/health",
        ):
            r = client.get(path, headers=_auth(r1))
            assert r.status_code in (403, 404), f"{path} leaked: {r.status_code}"
    finally:
        db.close()

"""
Tests for Recruiter OS Phase 1: role-specific screening on applications.

Covers:
  - Screening CRUD (partial update, mark_completed stamps)
  - Agency isolation (Agency A cannot access Agency B's screening data)
  - client_visibility enforcement via visibility.to_client_view
  - internal_notes never appears in the client view regardless of the map
  - MarketConfig endpoint returns supported countries with correct schemas
  - Screening blocked when the application has no linked role (400)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.recruiter.models import Agency, Application, CandidateProfile, Recruiter, Role
from app.recruiter.enums import ApplicationStage, RecruiterSeatRole
from app.recruiter.services.visibility import to_client_view
from app.core.security import hash_password, create_recruiter_access_token


client = TestClient(app)


def _mk_agency(db, name="Test Agency"):
    a = Agency(name=name, slug=name.lower().replace(" ", "-"))
    db.add(a); db.commit(); db.refresh(a)
    return a


def _mk_recruiter(db, agency, email="r@x.com"):
    r = Recruiter(
        agency_id=agency.id, email=email, full_name="Test Rec",
        role=RecruiterSeatRole.owner, password_hash=hash_password("pw"), is_active=True,
    )
    db.add(r); db.commit(); db.refresh(r)
    return r


def _mk_role_and_cand_app(db, agency, country="GB"):
    role = Role(agency_id=agency.id, title="Senior AI Engineer", country_code=country)
    db.add(role); db.commit(); db.refresh(role)
    cand = CandidateProfile(agency_id=agency.id, full_name="Deepraj Chouhan")
    db.add(cand); db.commit(); db.refresh(cand)
    app_row = Application(
        agency_id=agency.id, candidate_id=cand.id, role_id=role.id,
        stage=ApplicationStage.screening,
    )
    db.add(app_row); db.commit(); db.refresh(app_row)
    return role, cand, app_row


def _auth(recruiter):
    return {"Authorization": f"Bearer {create_recruiter_access_token(recruiter.id)}"}


def test_screening_patch_and_completion():
    db = SessionLocal()
    try:
        agency = _mk_agency(db, "AgencyA")
        rec = _mk_recruiter(db, agency, email="a@a.com")
        _, _, app_row = _mk_role_and_cand_app(db, agency)

        r = client.patch(
            f"/api/v1/recruiter/agencies/{agency.id}/applications/{app_row.id}/screening",
            headers=_auth(rec),
            json={
                "screening_outcome": "suitable",
                "recruiter_summary": "Strong AI engineer.",
                "internal_notes": "Note: prefers small teams.",
                "expected_compensation": {"amount": 95000, "currency": "GBP", "period": "YEAR"},
                "notice_period": {"value": 1, "unit": "MONTH", "negotiable": True},
                "availability_immediate": False,
                "preferred_work_model": "hybrid",
                "mark_completed": True,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["screening_outcome"] == "suitable"
        assert body["expected_compensation"]["amount"] == 95000
        assert body["notice_period"]["unit"] == "MONTH"
        assert body["preferred_work_model"] == "hybrid"
        assert body["screening_completed_at"] is not None
        assert body["screening_completed_by"] == rec.id
    finally:
        db.close()


def test_agency_isolation_screening():
    db = SessionLocal()
    try:
        a1 = _mk_agency(db, "IsoA"); r1 = _mk_recruiter(db, a1, email="iso-a@x.com")
        a2 = _mk_agency(db, "IsoB"); _r2 = _mk_recruiter(db, a2, email="iso-b@x.com")
        _, _, app_a = _mk_role_and_cand_app(db, a1)
        _, _, app_b = _mk_role_and_cand_app(db, a2)

        # r1 (agency A) cannot patch B's screening
        r = client.patch(
            f"/api/v1/recruiter/agencies/{a2.id}/applications/{app_b.id}/screening",
            headers=_auth(r1),
            json={"recruiter_summary": "hax"},
        )
        assert r.status_code in (403, 404)
    finally:
        db.close()


def test_client_view_hides_internal_notes():
    db = SessionLocal()
    try:
        agency = _mk_agency(db, "VisTest")
        rec = _mk_recruiter(db, agency, email="v@x.com")
        _, _, app_row = _mk_role_and_cand_app(db, agency)
        app_row.recruiter_summary = "Client-safe blurb."
        app_row.internal_notes = "SECRET — do not share."
        # Try to force internal_notes visibility on
        app_row.client_visibility = {"internal_notes": True, "recruiter_summary": True}
        db.commit()

        view = to_client_view(app_row)
        assert "internal_notes" not in view
        assert view["recruiter_summary"] == "Client-safe blurb."

        # Also via the HTTP endpoint
        r = client.get(
            f"/api/v1/recruiter/agencies/{agency.id}/applications/{app_row.id}/client-view",
            headers=_auth(rec),
        )
        assert r.status_code == 200
        assert "internal_notes" not in r.json()
    finally:
        db.close()


def test_market_config_endpoint():
    r = client.get("/api/v1/recruiter/market-configs")
    assert r.status_code == 200
    codes = {c["country_code"] for c in r.json()}
    assert {"GB", "IN", "AE"}.issubset(codes)

    r_uk = client.get("/api/v1/recruiter/market-configs/GB").json()
    assert r_uk["default_currency"] == "GBP"
    assert r_uk["salary_display_format"] == "annual"

    r_in = client.get("/api/v1/recruiter/market-configs/IN").json()
    assert r_in["salary_display_format"] == "lpa"

    r_ae = client.get("/api/v1/recruiter/market-configs/AE").json()
    assert r_ae["default_salary_period"] == "MONTH"


def test_screening_requires_role():
    db = SessionLocal()
    try:
        agency = _mk_agency(db, "NoRoleAg")
        rec = _mk_recruiter(db, agency, email="nr@x.com")
        cand = CandidateProfile(agency_id=agency.id, full_name="X")
        db.add(cand); db.commit(); db.refresh(cand)
        app_row = Application(
            agency_id=agency.id, candidate_id=cand.id, role_id=None,
            stage=ApplicationStage.sourced,
        )
        db.add(app_row); db.commit(); db.refresh(app_row)
        r = client.patch(
            f"/api/v1/recruiter/agencies/{agency.id}/applications/{app_row.id}/screening",
            headers=_auth(rec),
            json={"recruiter_summary": "x"},
        )
        assert r.status_code == 400
    finally:
        db.close()

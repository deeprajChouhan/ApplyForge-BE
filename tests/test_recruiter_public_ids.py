"""Recruiter API never exposes DB ints: GUIDs in, GUIDs out, raw ints rejected in URLs."""
from __future__ import annotations

import re
from datetime import datetime

from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.recruiter.api.auth_routes import issue_recruiter_tokens
from app.recruiter.enums import ApplicationStage, RecruiterSeatRole
from app.recruiter.ids import decode_id, encode_id as E, encode_payload
from app.recruiter.models import Agency, Application, CandidateProfile, Recruiter, Role

client = TestClient(app)
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def test_codec_roundtrip_and_tamper():
    for n in (1, 10, 32, 99999, 2**40):
        g = E(n)
        assert GUID.match(g) and decode_id(g) == n
    assert E(10) != E(11)
    g = E(10)
    bad = ("0" if g[0] != "0" else "1") + g[1:]
    try:
        decode_id(bad)
        raise AssertionError("tampered id accepted")
    except ValueError:
        pass


def test_encode_payload_nested():
    out = encode_payload({"id": 1, "role_id": None, "n": 5, "candidate_ids": [1, 2],
                          "journey": {"candidate": {"id": 3}}, "screening_completed_by": 4})
    assert out["id"] == E(1) and out["role_id"] is None and out["n"] == 5
    assert out["candidate_ids"] == [E(1), E(2)]
    assert out["journey"]["candidate"]["id"] == E(3)
    assert out["screening_completed_by"] == E(4)


def test_http_boundary():
    db = SessionLocal()
    ag = Agency(name="Ids Co", slug=f"ids-{datetime.utcnow().timestamp()}")
    db.add(ag); db.commit(); db.refresh(ag)
    rec = Recruiter(agency_id=ag.id, email=f"ids{ag.id}@x.com", full_name="R",
                    role=RecruiterSeatRole.owner, password_hash=hash_password("pw"), is_active=True)
    role = Role(agency_id=ag.id, title="Eng", country_code="GB")
    cand = CandidateProfile(agency_id=ag.id, full_name="C")
    db.add_all([rec, role, cand]); db.commit()
    app_row = Application(agency_id=ag.id, candidate_id=cand.id, role_id=role.id, stage=ApplicationStage.sourced)
    db.add(app_row); db.commit()
    for o in (rec, role, cand, app_row):
        db.refresh(o)
    h = {"Authorization": f"Bearer {issue_recruiter_tokens(rec.id)[0]}"}

    # Raw DB ids in the URL are refused.
    assert client.get(f"/api/v1/recruiter/agencies/{ag.id}/roles/{role.id}", headers=h).status_code == 404
    assert client.get(f"/api/v1/recruiter/agencies/{E(ag.id)}/roles/{role.id}", headers=h).status_code == 404

    r = client.get(f"/api/v1/recruiter/agencies/{E(ag.id)}/roles/{E(role.id)}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == E(role.id) and r.json()["agency_id"] == E(ag.id)

    me = client.get("/api/v1/recruiter/auth/me", headers=h).json()
    assert GUID.match(str(me["id"])) and me["agency"]["id"] == E(ag.id)

    # Query-string filters take GUIDs too.
    r = client.get(f"/api/v1/recruiter/agencies/{E(ag.id)}/submissions",
                   params={"candidate_id": E(cand.id), "role_id": E(role.id)}, headers=h)
    assert r.status_code == 200, r.text

    # GUIDs in JSON bodies are decoded.
    r = client.post(
        f"/api/v1/recruiter/agencies/{E(ag.id)}/roles/{E(role.id)}/pipeline/assign",
        json={"candidate_ids": [E(cand.id)], "stage": "sourced"}, headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["skipped_existing"] == [E(cand.id)]  # already in pipeline
    db.close()

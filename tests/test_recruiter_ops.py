"""
Operator Ops tools — duplicate-role detection, merge preview/apply,
careers-site reference link/unlink, audit history.

Offline (SQLite in-memory + mock AI). Run:
    cd backend && pytest tests/test_recruiter_ops.py -q
"""
import os
import tempfile
from datetime import timedelta

os.environ["EMBEDDING_PROVIDER"] = "mock"
os.environ.setdefault("UPLOAD_DIR", os.path.join(tempfile.gettempdir(), f"ops_pytest_uploads_{os.getpid()}"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.security import create_token, hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.models import User  # noqa: E402
from app.recruiter import models as M  # noqa: E402
from app.recruiter.enums import ApplicationStage, RoleStatus  # noqa: E402
from app.recruiter.ids import decode_id, encode_id  # noqa: E402

BASE = "/api/v1/recruiter"
OPS = f"{BASE}/admin/ops"
REF = "core-automotive-embedded-software-developer"
TITLE = "Core Automotive Embedded Software Developer"

_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=_engine)


def _override_get_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():  # overrides conftest's file-db fixture
    yield


@pytest.fixture(scope="module")
def client():
    prev = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    if prev is not None:
        app.dependency_overrides[get_db] = prev
    else:
        app.dependency_overrides.pop(get_db, None)


def _token(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_token(str(user_id), 'access', timedelta(minutes=30))}"}


@pytest.fixture(scope="module")
def env(client):
    db = Session()
    admin = User(email="ops@example.com", password_hash=hash_password("x" * 12), role=UserRole.admin)
    plain = User(email="plain@example.com", password_hash=hash_password("x" * 12))
    db.add_all([admin, plain])
    db.commit()
    admin_h, plain_h = _token(admin.id), _token(plain.id)
    a = client.post(f"{BASE}/admin/agencies", headers=admin_h, json={"name": "T1 Tech", "slug": "t1-tech"}).json()
    agency_id = decode_id(a["id"])

    keep = M.Role(agency_id=agency_id, title=TITLE, status=RoleStatus.open, source="app",
                  required_skills=["embedded c", "matlab"], description="Recruiter spec")
    dup = M.Role(agency_id=agency_id, title=TITLE, status=RoleStatus.closed, source="partner_api", external_ref=REF)
    other = M.Role(agency_id=agency_id, title="Embedded Linux / IoT Gateway Firmware Engineer", status=RoleStatus.open)
    db.add_all([keep, dup, other])
    db.flush()
    c1 = M.CandidateProfile(agency_id=agency_id, full_name="Deepraj Chouhan", email="d@example.com")
    c2 = M.CandidateProfile(agency_id=agency_id, full_name="Already There", email="a@example.com")
    db.add_all([c1, c2])
    db.flush()
    app_moving = M.Application(agency_id=agency_id, role_id=dup.id, candidate_id=c1.id,
                               stage=ApplicationStage.sourced, fit_score=68, source="partner_api")
    app_clash = M.Application(agency_id=agency_id, role_id=dup.id, candidate_id=c2.id, stage=ApplicationStage.sourced)
    app_existing = M.Application(agency_id=agency_id, role_id=keep.id, candidate_id=c2.id, stage=ApplicationStage.screening)
    db.add_all([app_moving, app_clash, app_existing])
    db.commit()
    out = {
        "admin_h": admin_h, "plain_h": plain_h, "agency": a["id"], "agency_int": agency_id,
        "keep": encode_id(keep.id), "dup": encode_id(dup.id), "other": encode_id(other.id),
        "keep_int": keep.id, "dup_int": dup.id,
        "app_moving": app_moving.id, "app_clash": app_clash.id,
    }
    db.close()
    return out


def test_ops_is_operator_only(client, env):
    url = f"{OPS}/agencies/{env['agency']}/duplicate-roles"
    assert client.get(url).status_code in (401, 403)
    assert client.get(url, headers=env["plain_h"]).status_code == 403


def test_duplicates_detected_with_suggested_keep(client, env):
    groups = client.get(f"{OPS}/agencies/{env['agency']}/duplicate-roles", headers=env["admin_h"]).json()
    assert len(groups) == 1
    g = groups[0]
    assert g["has_portal_copy"] is True
    assert g["suggested_keep_id"] == env["keep"]          # the app-built role, not the portal copy
    assert {r["id"] for r in g["roles"]} == {env["keep"], env["dup"]}
    dup = next(r for r in g["roles"] if r["id"] == env["dup"])
    assert dup["external_ref"] == REF and dup["applications"] == 2


def test_role_search(client, env):
    rows = client.get(f"{OPS}/agencies/{env['agency']}/roles", headers=env["admin_h"], params={"q": "iot"}).json()
    assert [r["id"] for r in rows] == [env["other"]]
    rows = client.get(f"{OPS}/agencies/{env['agency']}/roles", headers=env["admin_h"], params={"q": REF}).json()
    assert [r["id"] for r in rows] == [env["dup"]]


def test_merge_preview_writes_nothing(client, env):
    r = client.post(f"{OPS}/agencies/{env['agency']}/roles/merge", headers=env["admin_h"], json={
        "source_role_id": env["dup"], "target_role_id": env["keep"]})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["applied"] is False
    assert [a["candidate_name"] for a in p["applications_to_move"]] == ["Deepraj Chouhan"]
    assert [a["candidate_name"] for a in p["clashes"]] == ["Already There"]
    assert any("already applied" in w for w in p["warnings"])
    db = Session()
    assert db.get(M.Role, env["dup_int"]).external_ref == REF
    assert db.get(M.Application, env["app_moving"]).role_id == env["dup_int"]
    db.close()


def test_merge_validation(client, env):
    url = f"{OPS}/agencies/{env['agency']}/roles/merge"
    h = env["admin_h"]
    assert client.post(url, headers=h, json={"source_role_id": env["keep"], "target_role_id": env["keep"]}).status_code == 422
    # can't delete the duplicate while a clashing application is still on it
    assert client.post(url, headers=h, json={
        "source_role_id": env["dup"], "target_role_id": env["keep"], "delete_source": True}).status_code == 409


def test_merge_apply_moves_ref_and_application_and_audits(client, env):
    r = client.post(f"{OPS}/agencies/{env['agency']}/roles/merge", headers=env["admin_h"], json={
        "source_role_id": env["dup"], "target_role_id": env["keep"], "dry_run": False})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] is True
    db = Session()
    assert db.get(M.Role, env["dup_int"]).external_ref is None
    assert db.get(M.Role, env["keep_int"]).external_ref == REF
    assert db.get(M.Application, env["app_moving"]).role_id == env["keep_int"]
    assert db.get(M.Application, env["app_clash"]).role_id == env["dup_int"]     # clash left behind
    db.close()
    hist = client.get(f"{OPS}/agencies/{env['agency']}/history", headers=env["admin_h"]).json()
    assert hist[0]["action"] == "role.merged" and hist[0]["actor"] == "operator:ops@example.com"
    assert hist[0]["detail"]["external_ref"] == REF
    # no raw integer ids leak through the audit detail
    assert all(isinstance(v, str) for v in hist[0]["detail"]["moved_application_ids"])


def test_external_ref_link_unlink_and_conflict(client, env):
    url = lambda rid: f"{OPS}/agencies/{env['agency']}/roles/{rid}/external-ref"  # noqa: E731
    h = env["admin_h"]
    assert client.patch(url(env["other"]), headers=h, json={"external_ref": REF}).status_code == 409
    assert client.patch(url(env["other"]), headers=h, json={"external_ref": "bad ref!"}).status_code == 422
    r = client.patch(url(env["other"]), headers=h, json={"external_ref": "embedded-linux-iot"})
    assert r.status_code == 200 and r.json()["external_ref"] == "embedded-linux-iot"
    r = client.patch(url(env["other"]), headers=h, json={"external_ref": None})
    assert r.status_code == 200 and r.json()["external_ref"] is None
    actions = [x["action"] for x in client.get(f"{OPS}/agencies/{env['agency']}/history", headers=h).json()]
    assert actions[:2] == ["role.external_ref_cleared", "role.external_ref_set"]


def test_other_agency_roles_are_out_of_reach(client, env):
    b = client.post(f"{BASE}/admin/agencies", headers=env["admin_h"], json={"name": "Other", "slug": "other-ag"}).json()
    r = client.post(f"{OPS}/agencies/{b['id']}/roles/merge", headers=env["admin_h"], json={
        "source_role_id": env["dup"], "target_role_id": env["keep"]})
    assert r.status_code == 404

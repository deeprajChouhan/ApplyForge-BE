"""
Per-recruiter role visibility — an owner restricts a seat to selected roles and
the seat must never be able to see any other role or anything attached to it.

Offline (SQLite in-memory + mock AI). Run:
    cd backend && pytest tests/test_recruiter_role_visibility.py -q
"""
import json
import os
import tempfile
from datetime import timedelta

os.environ["EMBEDDING_PROVIDER"] = "mock"
os.environ.setdefault("UPLOAD_DIR", os.path.join(tempfile.gettempdir(), f"vis_pytest_uploads_{os.getpid()}"))

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
from app.recruiter import enums as E  # noqa: E402
from app.recruiter import models as M  # noqa: E402
from app.recruiter.ids import decode_id, encode_id  # noqa: E402

BASE = "/api/v1/recruiter"
SECRET_TITLE = "Confidential CFO Search"
OPEN_TITLE = "Core Automotive Embedded Software Developer"

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


def _first(enum_cls):
    return list(enum_cls)[0]


def _seed_role_world(db, agency_id: int, title: str, cand_name: str) -> dict:
    """A role with one of everything hanging off it."""
    role = M.Role(agency_id=agency_id, title=title, status=E.RoleStatus.open,
                  required_skills=["python"], description=f"{title} spec")
    cand = M.CandidateProfile(agency_id=agency_id, full_name=cand_name, email=f"{cand_name.split()[0].lower()}@x.example")
    db.add_all([role, cand])
    db.flush()
    app_row = M.Application(agency_id=agency_id, role_id=role.id, candidate_id=cand.id,
                            stage=E.ApplicationStage.screening, fit_score=80)
    sl = M.Shortlist(agency_id=agency_id, role_id=role.id)
    db.add_all([app_row, sl])
    db.flush()
    db.add(M.ShortlistEntry(shortlist_id=sl.id, candidate_id=cand.id, rank=1, fit_score=80))
    note = M.ApplicationNote(agency_id=agency_id, application_id=app_row.id, body=f"note about {title}")
    sub = M.ClientSubmission(agency_id=agency_id, role_id=role.id, candidate_id=cand.id,
                             application_id=app_row.id, status=_first(E.SubmissionStatus))
    iv = M.Interview(agency_id=agency_id, application_id=app_row.id, role_id=role.id,
                     candidate_id=cand.id, status=_first(E.InterviewStatus))
    off = M.Offer(agency_id=agency_id, application_id=app_row.id, role_id=role.id,
                  candidate_id=cand.id, status=_first(E.OfferStatus))
    db.add_all([note, sub, iv, off])
    db.flush()
    pl = M.Placement(agency_id=agency_id, role_id=role.id, candidate_id=cand.id,
                     offer_id=off.id, status=_first(E.PlacementStatus))
    cons = M.CandidateConsent(agency_id=agency_id, candidate_id=cand.id, role_id=role.id,
                              status=_first(E.ConsentStatus))
    db.add_all([pl, cons])
    db.flush()
    notif = M.Notification(agency_id=agency_id, kind=_first(E.NotificationKind), title=f"Update on {title}",
                           priority=_first(E.TaskPriority), parent_kind="application", parent_id=app_row.id)
    task = M.RecruiterTask(agency_id=agency_id, title=f"Chase {title}", status=_first(E.TaskStatus),
                           priority=_first(E.TaskPriority), ai_generated=False,
                           parent_kind="offer", parent_id=off.id)
    db.add_all([notif, task])
    db.flush()
    return {"role": role.id, "cand": cand.id, "app": app_row.id, "note": note.id, "sub": sub.id,
            "iv": iv.id, "offer": off.id, "placement": pl.id, "consent": cons.id, "notif": notif.id,
            "task": task.id, "shortlist": sl.id}


@pytest.fixture(scope="module")
def env(client):
    db = Session()
    admin = User(email="op@vis.example", password_hash=hash_password("x" * 12), role=UserRole.admin)
    db.add(admin)
    db.commit()
    admin_h = {"Authorization": f"Bearer {create_token(str(admin.id), 'access', timedelta(minutes=30))}"}
    db.close()
    a = client.post(f"{BASE}/admin/agencies", headers=admin_h, json={"name": "T1 Tech", "slug": "t1-vis"}).json()
    client.patch(f"{BASE}/admin/agencies/{a['id']}", headers=admin_h, json={"plan": "pro"})

    def seat(email, role="recruiter"):
        client.post(f"{BASE}/admin/recruiters", headers=admin_h, json={
            "agency_id": a["id"], "email": email, "full_name": email.split("@")[0], "password": "seatpass123", "role": role})
        tok = client.post(f"{BASE}/auth/login", json={"email": email, "password": "seatpass123"}).json()["access_token"]
        return {"Authorization": f"Bearer {tok}"}

    owner_h = seat("naushad@t1.example", "owner")
    torben_h = seat("torben@t1.example")
    kshitij_h = seat("kris@t1.example")
    team = client.get(f"{BASE}/agency/team", headers=owner_h).json()
    torben = next(m for m in team if m["email"] == "torben@t1.example")

    db = Session()
    agency_id = decode_id(a["id"])
    # Public GUIDs aren't namespaced by type, so offset id sequences in another
    # agency: then a hidden object's GUID can't collide with a shared candidate's.
    filler = M.Agency(name="Filler", slug="filler-vis")
    db.add(filler)
    db.flush()
    db.add_all([M.Role(agency_id=filler.id, title=f"f{i}") for i in range(20)])
    db.add_all([M.CandidateProfile(agency_id=filler.id, full_name=f"f{i}") for i in range(40)])
    db.flush()
    open_w = _seed_role_world(db, agency_id, OPEN_TITLE, "Deepraj Chouhan")
    secret_w = _seed_role_world(db, agency_id, SECRET_TITLE, "Secret Candidate")
    db.commit()
    db.close()

    # Owner restricts Torben to the automotive role only.
    r = client.put(f"{BASE}/agency/team/{torben['id']}/role-access", headers=owner_h,
                   json={"mode": "selected", "role_ids": [encode_id(open_w["role"])]})
    assert r.status_code == 200, r.text
    return {
        "agency": a["id"], "owner_h": owner_h, "torben_h": torben_h, "kshitij_h": kshitij_h,
        "torben_id": torben["id"], "admin_h": admin_h,
        "seat_ids": {m["id"] for m in team} | {a["id"]},
        "open": {k: encode_id(v) for k, v in open_w.items()},
        "secret": {k: encode_id(v) for k, v in secret_w.items()},
    }


def _url(env, path):
    return f"{BASE}/agencies/{env['agency']}{path}"


# ── owner controls ──────────────────────────────────────────────────────
def test_team_list_shows_access_mode(client, env):
    team = {m["email"]: m for m in client.get(f"{BASE}/agency/team", headers=env["owner_h"]).json()}
    assert team["torben@t1.example"]["role_access"] == "selected"
    assert team["torben@t1.example"]["granted_roles"] == 1
    assert team["kris@t1.example"]["role_access"] == "all"


def test_role_access_detail_lists_every_role_for_owner(client, env):
    r = client.get(f"{BASE}/agency/team/{env['torben_id']}/role-access", headers=env["owner_h"]).json()
    assert r["mode"] == "selected"
    granted = {x["title"]: x["granted"] for x in r["roles"]}
    assert granted == {OPEN_TITLE: True, SECRET_TITLE: False}


def test_only_owner_manages_access(client, env):
    url = f"{BASE}/agency/team/{env['torben_id']}/role-access"
    assert client.get(url, headers=env["kshitij_h"]).status_code == 403
    assert client.put(url, headers=env["torben_h"], json={"mode": "all"}).status_code == 403


def test_owner_cannot_be_restricted_and_foreign_roles_rejected(client, env):
    team = client.get(f"{BASE}/agency/team", headers=env["owner_h"]).json()
    owner = next(m for m in team if m["role"] == "owner")
    assert client.put(f"{BASE}/agency/team/{owner['id']}/role-access", headers=env["owner_h"],
                      json={"mode": "selected", "role_ids": []}).status_code == 400
    assert client.put(f"{BASE}/agency/team/{env['torben_id']}/role-access", headers=env["owner_h"],
                      json={"mode": "selected", "role_ids": [encode_id(999999)]}).status_code in (404, 422)


# ── the restricted seat can never see the hidden role ───────────────────
def _body(r) -> str:
    return json.dumps(r.json()) if r.headers.get("content-type", "").startswith("application/json") else r.text


LIST_ENDPOINTS = [
    "/roles", "/applications", "/candidates", "/interviews", "/offers", "/placements",
    "/submissions", "/consents", "/tasks", "/notifications", "/daily-brief", "/market",
]


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
def test_lists_never_mention_hidden_role(client, env, path):
    r = client.get(_url(env, path), headers=env["torben_h"])
    if r.status_code in (403, 404, 405, 422):
        pytest.skip(f"{path} -> {r.status_code}")
    assert r.status_code == 200, r.text
    body = _body(r)
    assert SECRET_TITLE not in body
    for k, v in env["secret"].items():
        if k == "cand" or v in env["seat_ids"]:  # shared pool / the caller's own ids, not role data
            continue
        assert v not in body, f"{path} leaked hidden {k}"


def test_restricted_seat_sees_granted_role(client, env):
    titles = [r["title"] for r in client.get(_url(env, "/roles"), headers=env["torben_h"]).json()]
    assert titles == [OPEN_TITLE]
    assert client.get(_url(env, f"/roles/{env['open']['role']}"), headers=env["torben_h"]).status_code == 200


def test_unrestricted_seat_and_owner_see_everything(client, env):
    for h in (env["kshitij_h"], env["owner_h"], env["admin_h"]):
        titles = {r["title"] for r in client.get(_url(env, "/roles"), headers=h).json()}
        assert titles == {OPEN_TITLE, SECRET_TITLE}


DETAIL_ENDPOINTS = [
    ("get", "/roles/{role}"),
    ("get", "/roles/{role}/pipeline"),
    ("get", "/roles/{role}/shortlist/latest"),
    ("get", "/roles/{role}/share"),
    ("get", "/roles/{role}/feedback"),
    ("get", "/roles/{role}/proposal.pdf"),
    ("get", "/roles/{role}/placement-journey"),
    ("get", "/intelligence/roles/{role}/health"),
    ("get", "/intelligence/roles/{role}/quality-check"),
    ("get", "/applications/{app}/notes"),
    ("get", "/applications/{app}/screening"),
    ("get", "/interviews/{iv}"),
    ("get", "/submissions/{sub}"),
    ("patch", "/roles/{role}"),
    ("patch", "/applications/{app}/stage"),
    ("patch", "/interviews/{iv}"),
    ("patch", "/offers/{offer}"),
    ("patch", "/placements/{placement}"),
    ("patch", "/submissions/{sub}"),
    ("patch", "/tasks/{task}"),
    ("post", "/applications/{app}/notes"),
    ("post", "/roles/{role}/listing"),
    ("post", "/roles/{role}/share"),
]

PAYLOADS = {
    "/roles/{role}": {"title": "pwned"},
    "/applications/{app}/stage": {"stage": "rejected"},
    "/applications/{app}/notes": {"body": "x"},
}


@pytest.mark.parametrize("method,path", DETAIL_ENDPOINTS)
def test_hidden_role_objects_are_not_found(client, env, method, path):
    url = _url(env, path.format(**env["secret"]))
    r = getattr(client, method)(url, headers=env["torben_h"], **({} if method == "get" else {"json": PAYLOADS.get(path, {})}))
    assert r.status_code in (404, 422), f"{method.upper()} {path} -> {r.status_code}: {r.text[:200]}"
    if r.status_code == 422:  # validation failed before lookup — must not echo hidden data either
        assert SECRET_TITLE not in r.text


def test_hidden_role_untouched_after_attempts(client, env):
    r = client.get(_url(env, f"/roles/{env['secret']['role']}"), headers=env["owner_h"]).json()
    assert r["title"] == SECRET_TITLE


def test_candidate_detail_hides_hidden_applications(client, env):
    # The candidate pool is shared, but their journey on a hidden role is not.
    r = client.get(_url(env, f"/candidates/{env['secret']['cand']}"), headers=env["torben_h"])
    assert r.status_code == 200
    assert SECRET_TITLE not in _body(r)
    assert env["secret"]["app"] not in _body(r)


def test_cannot_attach_candidates_to_hidden_role(client, env):
    r = client.post(_url(env, f"/roles/{env['secret']['role']}/pipeline/assign"), headers=env["torben_h"],
                    json={"candidate_ids": [env["open"]["cand"]]})
    assert r.status_code in (404, 422)
    r = client.post(_url(env, "/applications"), headers=env["torben_h"],
                    json={"role_id": env["secret"]["role"], "candidate_id": env["open"]["cand"]})
    assert r.status_code in (404, 422)


def test_role_created_by_restricted_seat_is_visible_to_it(client, env):
    r = client.post(_url(env, "/roles"), headers=env["torben_h"], json={"title": "Torben's own role"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert client.get(_url(env, f"/roles/{rid}"), headers=env["torben_h"]).status_code == 200
    titles = {x["title"] for x in client.get(_url(env, "/roles"), headers=env["torben_h"]).json()}
    assert titles == {OPEN_TITLE, "Torben's own role"}


def test_switching_back_to_all_restores_visibility(client, env):
    url = f"{BASE}/agency/team/{env['torben_id']}/role-access"
    assert client.put(url, headers=env["owner_h"], json={"mode": "all"}).status_code == 200
    titles = {x["title"] for x in client.get(_url(env, "/roles"), headers=env["torben_h"]).json()}
    assert SECRET_TITLE in titles
    # restore for any later test
    client.put(url, headers=env["owner_h"], json={"mode": "selected", "role_ids": [env["open"]["role"]]})


POSITIVE = [
    ("/applications", "app"), ("/interviews", "iv"), ("/offers", "offer"), ("/placements", "placement"),
    ("/submissions", "sub"), ("/notifications", "notif"), ("/tasks", "task"),
]


@pytest.mark.parametrize("path,key", POSITIVE)
def test_filter_is_not_blanket(client, env, path, key):
    """Restricted seat still sees its granted role's data; unrestricted seat sees both."""
    mine = _body(client.get(_url(env, path), headers=env["torben_h"]))
    assert env["open"][key] in mine, f"{path}: granted {key} missing for restricted seat"
    everyone = _body(client.get(_url(env, path), headers=env["kshitij_h"]))
    assert env["open"][key] in everyone and env["secret"][key] in everyone


def test_external_ref_clash_with_hidden_role_is_409_without_title(client, env):
    owner_h, torben_h = env["owner_h"], env["torben_h"]
    assert client.patch(_url(env, f"/roles/{env['secret']['role']}"), headers=owner_h,
                        json={"external_ref": "cfo-search"}).status_code == 200
    r = client.patch(_url(env, f"/roles/{env['open']['role']}"), headers=torben_h, json={"external_ref": "cfo-search"})
    assert r.status_code == 409 and SECRET_TITLE not in r.text

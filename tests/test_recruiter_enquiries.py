"""
Website enquiries — public contact form + operator inbox.

Offline (SQLite in-memory). Run:
    cd backend && pytest tests/test_recruiter_enquiries.py -q
"""
import os
from datetime import timedelta

os.environ["EMBEDDING_PROVIDER"] = "mock"

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
from app.recruiter.api import enquiry_routes  # noqa: E402
from app.recruiter.partner import ratelimit  # noqa: E402

BASE = "/api/v1/recruiter"
PUBLIC = f"{BASE}/public/enquiries"
ADMIN = f"{BASE}/admin/enquiries"

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
    ratelimit.reset_all()
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
def headers(client):
    db = Session()
    admin = User(email="enq-admin@example.com", password_hash=hash_password("x" * 12), role=UserRole.admin)
    plain = User(email="enq-plain@example.com", password_hash=hash_password("x" * 12))
    db.add_all([admin, plain])
    db.commit()
    out = {"admin": _token(admin.id), "plain": _token(plain.id)}
    db.close()
    return out


def _payload(**kw):
    base = {
        "topic": "api",
        "full_name": "  Ada Lovelace ",
        "email": "Ada@Example.com",
        "company": "Analytical Engines Ltd",
        "company_type": "platform",
        "message": "We'd like ranked applicants inside our careers portal.",
        "source_page": "/contact?topic=api",
    }
    base.update(kw)
    return base


def test_public_enquiry_is_stored_and_listed(client, headers):
    r = client.post(PUBLIC, json=_payload())
    assert r.status_code == 202, r.text
    assert r.json() == {"ok": True}

    rows = client.get(ADMIN, headers=headers["admin"]).json()
    assert len(rows) >= 1
    row = rows[0]
    assert row["full_name"] == "Ada Lovelace"
    assert row["email"] == "ada@example.com"
    assert row["status"] == "new"
    assert row["topic"] == "api"
    # Public ids, never raw ints.
    assert isinstance(row["id"], str) and "-" in row["id"]


def test_honeypot_is_accepted_but_not_stored(client, headers):
    before = client.get(f"{ADMIN}/summary", headers=headers["admin"]).json()["total"]
    r = client.post(PUBLIC, json=_payload(website="http://spam.example", email="bot@example.com"))
    assert r.status_code == 202
    after = client.get(f"{ADMIN}/summary", headers=headers["admin"]).json()["total"]
    assert after == before


def test_validation_rejects_short_message_and_bad_email(client):
    assert client.post(PUBLIC, json=_payload(message="hi")).status_code == 422
    assert client.post(PUBLIC, json=_payload(email="not-an-email")).status_code == 422
    assert client.post(PUBLIC, json=_payload(topic="free-money")).status_code == 422


def test_rate_limited_per_ip(client):
    for _ in range(enquiry_routes.ENQUIRY_RATE_LIMIT):
        assert client.post(PUBLIC, json=_payload()).status_code == 202
    r = client.post(PUBLIC, json=_payload())
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_admin_endpoints_are_operator_only(client, headers):
    assert client.get(ADMIN).status_code in (401, 403)
    assert client.get(ADMIN, headers=headers["plain"]).status_code == 403
    assert client.get(f"{ADMIN}/summary", headers=headers["plain"]).status_code == 403


def test_triage_update_filter_and_delete(client, headers):
    h = headers["admin"]
    client.post(PUBLIC, json=_payload(full_name="Grace Hopper", email="grace@example.com", topic="demo", company_type="agency"))
    rows = client.get(ADMIN, headers=h, params={"search": "grace"}).json()
    assert [r["full_name"] for r in rows] == ["Grace Hopper"]
    eid = rows[0]["id"]

    r = client.patch(f"{ADMIN}/{eid}", headers=h, json={"status": "contacted", "admin_notes": "  Call booked Fri  "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "contacted"
    assert body["admin_notes"] == "Call booked Fri"
    assert body["handled_at"] is not None

    assert client.patch(f"{ADMIN}/{eid}", headers=h, json={"status": "bogus"}).status_code == 422
    assert [r["id"] for r in client.get(ADMIN, headers=h, params={"status": "contacted"}).json()] == [eid]

    summary = client.get(f"{ADMIN}/summary", headers=h).json()
    assert summary["by_status"]["contacted"] >= 1
    assert set(summary["by_status"]) == {"new", "contacted", "qualified", "closed", "spam"}

    assert client.delete(f"{ADMIN}/{eid}", headers=h).status_code == 204
    assert client.delete(f"{ADMIN}/{eid}", headers=h).status_code == 404


def test_enquiries_table_is_inside_the_data_wall():
    t = M.Enquiry.__table__
    assert t.name.startswith("rec_")
    assert not t.foreign_keys
    assert t in M.RECRUITER_TABLES

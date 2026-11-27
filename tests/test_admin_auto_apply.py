"""Admin auto-apply controls: list, per-user state, disable one, bulk disable."""
from app.models.auto_apply import AutoApplySettings
from app.models.enums import UserRole
from app.models.models import AuditLog, JobApplication, User
from tests.conftest import TestingSessionLocal, auth_headers

BASE = "/api/v1/admin"


def _admin_headers(client):
    h = auth_headers(client, email="admin@example.com")
    with TestingSessionLocal() as db:
        u = db.query(User).filter(User.email == "admin@example.com").one()
        u.role = UserRole.admin
        db.commit()
    return h


def _seed_user(client, email, stages):
    auth_headers(client, email=email)
    with TestingSessionLocal() as db:
        uid = db.query(User.id).filter(User.email == email).scalar()
        db.add(AutoApplySettings(user_id=uid, is_active=True, daily_cap=10, weekly_cap=50))
        for st in stages:
            db.add(JobApplication(user_id=uid, company_name="c", role_title="r",
                                  job_description="d", auto_apply_stage=st))
        db.commit()
        return uid


def test_list_and_state(client):
    h = _admin_headers(client)
    uid = _seed_user(client, "a@x.com", ["submitted", "awaiting_review", "queued"])
    r = client.get(f"{BASE}/auto-apply/users", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["running"] == 1 and body["total"] == 1
    c = body["items"][0]["counts"]
    assert (c["total"], c["submitted"], c["unsent"], c["in_flight"]) == (3, 1, 2, 1)

    r = client.get(f"{BASE}/users/{uid}/auto-apply", headers=h)
    assert r.json()["has_settings"] is True and r.json()["user"]["is_active"] is True


def test_disable_one_keeps_submitted(client):
    h = _admin_headers(client)
    uid = _seed_user(client, "b@x.com", ["submitted", "awaiting_review", "queued"])
    r = client.post(f"{BASE}/users/{uid}/auto-apply/disable", headers=h,
                    json={"cancel_in_flight": True, "remove_unsent_drafts": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"users_disabled": 1, "in_flight_cancelled": 1, "drafts_removed": 2}
    with TestingSessionLocal() as db:
        s = db.query(AutoApplySettings).filter_by(user_id=uid).one()
        assert s.is_active is False and s.paused_at is not None
        live = db.query(JobApplication).filter_by(user_id=uid, deleted_at=None).all()
        assert [a.auto_apply_stage for a in live] == ["submitted"]
        assert db.query(AuditLog).filter_by(action="admin.user.auto_apply_disable").count() == 1


def test_bulk_disable_all(client):
    h = _admin_headers(client)
    _seed_user(client, "c@x.com", ["queued"])
    _seed_user(client, "d@x.com", [])
    r = client.post(f"{BASE}/auto-apply/disable", headers=h, json={"all": True})
    assert r.status_code == 200, r.text
    assert r.json()["users_disabled"] == 2 and r.json()["in_flight_cancelled"] == 1
    assert client.get(f"{BASE}/auto-apply/users", headers=h).json()["running"] == 0


def test_requires_admin(client):
    h = auth_headers(client, email="plain@x.com")
    assert client.get(f"{BASE}/auto-apply/users", headers=h).status_code == 403
    assert client.post(f"{BASE}/auto-apply/disable", headers=h, json={"all": True}).status_code == 403

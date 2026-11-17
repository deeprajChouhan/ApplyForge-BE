"""Admin: delete crawled / discovered jobs older than N hours."""
from datetime import datetime, timedelta

from app.models.company import Company
from app.models.enums import UserRole
from app.models.job import Job
from app.models.job_source import JobSource
from app.models.models import CrawledJob, JobApplication, User
from tests.conftest import TestingSessionLocal, auth_headers


def _admin_headers(client):
    headers = auth_headers(client, email="admin@example.com")
    with TestingSessionLocal() as db:
        u = db.query(User).filter(User.email == "admin@example.com").one()
        u.role = UserRole.admin
        db.commit()
    return headers


def _seed():
    old = datetime.utcnow() - timedelta(hours=30)
    new = datetime.utcnow() - timedelta(hours=2)
    with TestingSessionLocal() as db:
        user = db.query(User).filter(User.email == "admin@example.com").one()
        co = Company(name="Acme", ats_provider="greenhouse", ats_slug="acme")
        db.add(co)
        db.flush()

        def job(ext, seen):
            j = Job(company_id=co.id, ats_provider="greenhouse", external_id=ext, title=ext,
                    description="d", apply_url="https://x", first_seen_at=seen, last_seen_at=seen)
            db.add(j)
            db.flush()
            db.add(JobSource(job_id=j.id, source="greenhouse", source_url="https://x"))
            return j

        job("old-free", old)
        applied = job("old-applied", old)
        job("fresh", new)
        db.add(JobApplication(user_id=user.id, company_name="Acme", role_title="x",
                              job_description="d", job_id=applied.id))

        def cj(ext, at, **kw):
            db.add(CrawledJob(user_id=user.id, source="remoteok", external_id=ext, title=ext,
                              company="Acme", apply_url="https://x", crawled_at=at, **kw))

        cj("c-old", old)
        cj("c-old-saved", old, is_saved=True)
        cj("c-old-dismissed", old, is_dismissed=True)
        cj("c-fresh", new)
        db.commit()


def test_preview_and_purge(client):
    headers = _admin_headers(client)
    _seed()

    r = client.get("/api/v1/admin/crawled-jobs/cleanup/preview", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["discovered_jobs"] == {"total": 3, "eligible": 1, "protected": 1}
    assert body["user_crawled_jobs"] == {"total": 4, "eligible": 1, "protected": 2}

    r = client.post("/api/v1/admin/crawled-jobs/cleanup", headers=headers, json={"older_than_hours": 24})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["deleted_discovered_jobs"] == 1
    assert res["deleted_job_sources"] == 1
    assert res["deleted_user_crawled_jobs"] == 1

    with TestingSessionLocal() as db:
        assert sorted(j.external_id for j in db.query(Job).all()) == ["fresh", "old-applied"]
        assert db.query(JobSource).count() == 2
        assert sorted(c.external_id for c in db.query(CrawledJob).all()) == [
            "c-fresh", "c-old-dismissed", "c-old-saved"]


def test_scope_flags(client):
    headers = _admin_headers(client)
    _seed()
    r = client.post("/api/v1/admin/crawled-jobs/cleanup", headers=headers,
                    json={"older_than_hours": 24, "include_discovered": False})
    assert r.json()["deleted_discovered_jobs"] == 0
    assert r.json()["deleted_user_crawled_jobs"] == 1


def test_requires_admin(client):
    headers = auth_headers(client, email="plain@example.com")
    assert client.get("/api/v1/admin/crawled-jobs/cleanup/preview", headers=headers).status_code == 403
    assert client.post("/api/v1/admin/crawled-jobs/cleanup", headers=headers, json={}).status_code == 403

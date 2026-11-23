"""Job Hunting → Extension settings and the settings-aware score-preview / save."""
import json

from app.models.models import JobApplication, ParsedResumeData, ProductEvent, User
from tests.conftest import TestingSessionLocal, auth_headers

API = "/api/v1/extension"

JD = (
    "We are hiring a Senior Backend Engineer. You will build services in Python and FastAPI, "
    "deploy on Kubernetes and AWS, and work with PostgreSQL and Redis. 5+ years of experience "
    "required. Hybrid role in Dubai, full-time. Experience with Java is a plus. " * 3
)


def _add_resume(email: str, skills: list[str]) -> int:
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter_by(email=email).first()
        row = ParsedResumeData(
            user_id=user.id,
            raw_text="resume",
            structured_json=json.dumps({"skills": skills}),
            confidence_score=0.9,
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def test_defaults(client):
    h = auth_headers(client)
    res = client.get(f"{API}/settings", headers=h)
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is True
    assert body["enabled_sites"] == ["linkedin", "indeed", "glassdoor", "other"]
    assert body["widget_position"] == "inline"
    assert body["default_resume_id"] is None
    assert body["default_resume_label"] is None


def test_update_validates_and_cleans(client):
    h = auth_headers(client)
    res = client.put(
        f"{API}/settings",
        headers=h,
        json={
            "enabled_sites": ["LinkedIn", "monster", "indeed"],
            "must_have_keywords": [" Python ", "python", "Remote"],
            "widget_position": "top-right",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["enabled_sites"] == ["linkedin", "indeed"]
    assert body["must_have_keywords"] == ["Python", "Remote"]
    assert body["widget_position"] == "top-right"

    bad = client.put(f"{API}/settings", headers=h, json={"widget_position": "middle"})
    assert bad.status_code == 422


def test_resume_must_belong_to_user(client):
    h1 = auth_headers(client, email="a@example.com")
    auth_headers(client, email="b@example.com")
    other = _add_resume("b@example.com", ["python"])
    res = client.put(f"{API}/settings", headers=h1, json={"default_resume_id": other})
    assert res.status_code == 404


def test_score_preview_uses_resume_and_settings(client):
    h = auth_headers(client, email="c@example.com")
    rid = _add_resume("c@example.com", ["Python", "FastAPI", "JavaScript"])
    client.put(
        f"{API}/settings",
        headers=h,
        json={
            "default_resume_id": rid,
            "must_have_keywords": ["hybrid", "visa sponsorship"],
            "excluded_companies": ["Acme"],
        },
    )
    res = client.post(
        f"{API}/jobs/score-preview",
        headers=h,
        json={"title": "Senior Backend Engineer", "company": "Acme Corp", "description": JD,
              "source_url": "https://www.linkedin.com/jobs/view/123/", "source_site": "linkedin"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["fit_source"] == "resume_quick"
    assert body["resume_id"] == rid
    assert "python" in body["matched_skills"]
    # word-boundary: "JavaScript" on the resume must not count as "java"
    assert "java" in body["missing_skills"]
    assert body["must_have_found"] == ["hybrid"]
    assert body["must_have_missing"] == ["visa sponsorship"]
    assert body["excluded_company"] is True
    assert body["already_saved_application_id"] is None


def test_save_pins_resume_status_and_flags_duplicate(client):
    h = auth_headers(client, email="d@example.com")
    rid = _add_resume("d@example.com", ["python"])
    client.put(f"{API}/settings", headers=h, json={"default_resume_id": rid, "default_save_status": "ready"})
    url = "https://www.linkedin.com/jobs/view/999/"
    saved = client.post(
        f"{API}/jobs/save", headers=h,
        json={"title": "Engineer", "company": "Murphy AI", "description": JD, "source_url": url},
    )
    assert saved.status_code == 200, saved.text
    app_id = int(saved.json()["application_id"])

    db = TestingSessionLocal()
    try:
        app = db.query(JobApplication).filter_by(id=app_id).first()
        assert app.selected_resume_id == rid
        assert app.status.value == "ready"
    finally:
        db.close()

    prev = client.post(
        f"{API}/jobs/score-preview", headers=h,
        json={"title": "Engineer", "company": "Murphy AI", "description": JD, "source_url": url.rstrip("/")},
    )
    assert prev.json()["already_saved_application_id"] == str(app_id)


def test_no_resume_stays_neutral(client):
    h = auth_headers(client, email="e@example.com")
    res = client.post(f"{API}/jobs/score-preview", headers=h, json={"title": "Engineer", "company": "X", "description": JD})
    body = res.json()
    assert body["fit_source"] == "neutral"
    assert body["fit_score"] == 50.0


def test_web_preview_is_not_extension_activity(client):
    """The web app's New application sheet must not mark the extension connected."""
    h = auth_headers(client, email="web@example.com")
    _add_resume("web@example.com", ["Python", "FastAPI"])
    res = client.post(
        f"{API}/jobs/score-preview",
        headers=h,
        json={"title": "Backend Engineer", "company": "X", "description": JD, "source": "web"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["fit_source"] == "resume_quick"

    status = client.get(f"{API}/status", headers=h).json()
    assert status["connected"] is False
    assert status["last_seen_at"] is None

    db = TestingSessionLocal()
    try:
        names = [e.event_name for e in db.query(ProductEvent).all()]
    finally:
        db.close()
    assert "web_job_scored" in names
    assert "extension_job_scored" not in names


def test_extension_preview_still_marks_connected(client):
    h = auth_headers(client, email="ext@example.com")
    client.post(f"{API}/jobs/score-preview", headers=h, json={"title": "Engineer", "company": "X", "description": JD})
    assert client.get(f"{API}/status", headers=h).json()["connected"] is True


def test_preview_rejects_unknown_source(client):
    h = auth_headers(client, email="bad@example.com")
    res = client.post(f"{API}/jobs/score-preview", headers=h, json={"title": "E", "company": "X", "source": "mobile"})
    assert res.status_code == 422

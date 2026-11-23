"""Application notes (PATCH /applications/{id} notes) and updated_at on ApplicationOut."""
from .conftest import auth_headers

API = "/api/v1/applications"


def _create(client, h):
    r = client.post(API, json={"company_name": "Acme", "role_title": "Engineer", "job_description": "Need Python"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_notes_round_trip_and_clear(client):
    h = auth_headers(client, "notes-user@test.com")
    app_id = _create(client, h)
    assert client.get(f"{API}/{app_id}", headers=h).json()["notes"] is None

    r = client.patch(f"{API}/{app_id}", json={"notes": "Recruiter: Sam. Call Thu."}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == "Recruiter: Sam. Call Thu."

    # Other fields untouched; omitting notes keeps them.
    r = client.patch(f"{API}/{app_id}", json={"role_title": "Senior Engineer"}, headers=h)
    assert r.json()["notes"] == "Recruiter: Sam. Call Thu."
    assert r.json()["role_title"] == "Senior Engineer"

    # Empty string clears.
    r = client.patch(f"{API}/{app_id}", json={"notes": ""}, headers=h)
    assert r.json()["notes"] == ""


def test_notes_are_user_scoped(client):
    owner = auth_headers(client, "notes-owner@test.com")
    other = auth_headers(client, "notes-other@test.com")
    app_id = _create(client, owner)
    r = client.patch(f"{API}/{app_id}", json={"notes": "mine"}, headers=other)
    assert r.status_code == 404
    assert client.get(f"{API}/{app_id}", headers=owner).json()["notes"] is None


def test_notes_length_limit(client):
    h = auth_headers(client, "notes-long@test.com")
    app_id = _create(client, h)
    r = client.patch(f"{API}/{app_id}", json={"notes": "x" * 20001}, headers=h)
    assert r.status_code == 422


def test_application_out_has_updated_at(client):
    h = auth_headers(client, "notes-ts@test.com")
    app_id = _create(client, h)
    body = client.get(f"{API}/{app_id}", headers=h).json()
    assert body["updated_at"] is not None
    listed = client.get(API, headers=h).json()["items"][0]
    assert listed["updated_at"] is not None

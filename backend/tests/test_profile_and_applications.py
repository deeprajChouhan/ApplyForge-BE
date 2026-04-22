from .conftest import auth_headers


def test_profile_crud_and_isolation(client):
    h1 = auth_headers(client, "u1@test.com")
    h2 = auth_headers(client, "u2@test.com")
    p = client.put('/api/v1/profile', json={"full_name":"User One"}, headers=h1)
    assert p.status_code == 200
    e = client.post('/api/v1/profile/experiences', json={"company":"X","role":"Eng"}, headers=h1)
    eid = e.json()["id"]
    forbidden = client.put(f'/api/v1/profile/experiences/{eid}', json={"company":"Y","role":"Mgr"}, headers=h2)
    assert forbidden.status_code == 404


def test_application_status_and_generation(client):
    h = auth_headers(client, "u3@test.com")
    client.put('/api/v1/profile', json={"full_name":"User Three","summary":"Python FastAPI"}, headers=h)
    client.post('/api/v1/profile/skills', json={"name":"Python"}, headers=h)
    client.post('/api/v1/profile/knowledge/rebuild', headers=h)
    a = client.post('/api/v1/applications', json={"company_name":"Acme","role_title":"BE","job_description":"Need Python"}, headers=h)
    aid = a.json()["id"]
    s = client.post(f'/api/v1/applications/{aid}/status', json={"status":"applied"}, headers=h)
    assert s.status_code == 200
    g = client.post(f'/api/v1/applications/{aid}/generate', json={"doc_types":["resume"]}, headers=h)
    assert g.status_code == 200
    assert g.json()[0]["content"].startswith("[MOCK_GENERATION]")

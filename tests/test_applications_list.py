from .conftest import auth_headers


def test_applications_list_pagination_and_search(client):
    h = auth_headers(client, "list-user@test.com")
    client.post('/api/v1/applications', json={"company_name": "Acme", "role_title": "Backend Engineer", "job_description": "Need Python"}, headers=h)
    client.post('/api/v1/applications', json={"company_name": "Globex", "role_title": "Frontend Engineer", "job_description": "Need React"}, headers=h)

    r = client.get('/api/v1/applications', headers=h)
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"items", "total", "page", "page_size"}
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert len(body["items"]) == 2

    r = client.get('/api/v1/applications', params={"search": "acme"}, headers=h)
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["company_name"] == "Acme"

    r = client.get('/api/v1/applications', params={"page": 1, "page_size": 1}, headers=h)
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1

    r = client.get('/api/v1/applications', params={"status": "draft"}, headers=h)
    body = r.json()
    assert body["total"] == 2

def test_register_login_me(client):
    r = client.post('/api/v1/auth/register', json={"email":"a@a.com","password":"password123"})
    assert r.status_code == 200
    l = client.post('/api/v1/auth/login', json={"email":"a@a.com","password":"password123"})
    assert l.status_code == 200
    token = l.json()["access_token"]
    me = client.get('/api/v1/auth/me', headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200

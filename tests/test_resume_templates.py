"""
End-to-end tests for the resume-template system.

Covers the 12 acceptance cases from the spec:

 1. User default = Modern → new applications start with Modern.
 2. Change application template to Executive → reload keeps Executive.
 3. Export PDF → renderer resolves Executive.
 4. Export DOCX → renderer resolves Executive.
 5. Application A template selection does NOT alter the user's default.
 6. Application B starts with the user's actual default.
 7. Set Executive as account default → newly generated Application C uses Executive.
 8. Reordered sections appear in the requested order in the resolved config.
 9. Hidden section is absent from the resolved config's enabled section list.
10. User A cannot read/update/delete/use User B's template.
11. Attempting to set another user's template on an application → 404.
12. Deleting the current default template safely falls back.
"""
from __future__ import annotations

import io

import pytest

from .conftest import auth_headers


def _mk_app(client, headers, company="Acme", role="Backend Engineer",
            jd="Python fastapi rag qdrant"):
    r = client.post(
        "/api/v1/applications",
        json={"company_name": company, "role_title": role, "job_description": jd},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _list_templates(client, headers):
    r = client.get("/api/v1/resume-templates", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _template_by_base(body, base):
    for t in body["templates"]:
        if t["base_template"] == base and t["is_system"]:
            return t
    raise AssertionError(f"system template {base} missing from list")


# ── 1, 5, 6, 7 ────────────────────────────────────────────────────────────

def test_new_application_uses_user_default(client):
    h = auth_headers(client, "u1@test.com")
    body = _list_templates(client, h)
    modern = _template_by_base(body, "modern")

    # (1) Set Modern as default.
    r = client.post("/api/v1/resume-templates/default",
                    json={"template_id": modern["id"]}, headers=h)
    assert r.status_code == 200
    assert r.json()["is_default"] is True

    # New application → resolves to Modern.
    app_a = _mk_app(client, h)
    r = client.get(f"/api/v1/applications/{app_a}/template", headers=h)
    assert r.status_code == 200
    resolved = r.json()["resolved"]
    assert resolved["base_template"] == "modern"
    assert resolved["source"] in ("user_default", "application")

    # (5) Change template on Application A → default unchanged.
    executive = _template_by_base(body, "executive")
    r = client.patch(f"/api/v1/applications/{app_a}/template",
                     json={"template_id": executive["id"]}, headers=h)
    assert r.status_code == 200
    r = client.get("/api/v1/resume-templates", headers=h)
    assert r.json()["default_template_id"] == modern["id"]

    # (6) Application B starts with the user's default (Modern).
    app_b = _mk_app(client, h, company="Globex")
    r = client.get(f"/api/v1/applications/{app_b}/template", headers=h)
    assert r.json()["resolved"]["base_template"] == "modern"

    # (7) Change default to Executive → Application C uses Executive.
    r = client.post("/api/v1/resume-templates/default",
                    json={"template_id": executive["id"]}, headers=h)
    assert r.status_code == 200
    app_c = _mk_app(client, h, company="Initech")
    r = client.get(f"/api/v1/applications/{app_c}/template", headers=h)
    assert r.json()["resolved"]["base_template"] == "executive"


# ── 2, 3, 4 ───────────────────────────────────────────────────────────────

def test_reload_and_exports_use_persisted_template(client):
    h = auth_headers(client, "u-exports@test.com")
    body = _list_templates(client, h)
    executive = _template_by_base(body, "executive")

    app_id = _mk_app(client, h)

    # (2) Select Executive, then read the app's template back.
    r = client.patch(f"/api/v1/applications/{app_id}/template",
                     json={"template_id": executive["id"]}, headers=h)
    assert r.status_code == 200

    r = client.get(f"/api/v1/applications/{app_id}/template", headers=h)
    assert r.status_code == 200
    resolved = r.json()["resolved"]
    assert resolved["base_template"] == "executive"
    assert resolved["source"] == "application"

    # (3) PDF export — bytes come back and the file is a valid PDF.
    r = client.get(f"/api/v1/applications/{app_id}/export/pdf", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:4] == b"%PDF"

    # (4) DOCX export — bytes come back and the file is a valid DOCX (zip).
    r = client.get(f"/api/v1/applications/{app_id}/export/docx", headers=h)
    assert r.status_code == 200
    assert r.content[:2] == b"PK"


# ── 8, 9 ──────────────────────────────────────────────────────────────────

def test_reorder_and_hide_reflected_in_config(client):
    h = auth_headers(client, "u-edit@test.com")
    body = _list_templates(client, h)
    modern = _template_by_base(body, "modern")

    # Duplicate to get an editable copy.
    r = client.post(f"/api/v1/resume-templates/{modern['id']}/duplicate",
                    json={"name": "My Modern"}, headers=h)
    assert r.status_code == 201
    dup = r.json()

    # Reorder and hide.
    new_sections = [dict(s) for s in dup["config"]["sections"]]
    # Move projects to the top after header, hide certifications.
    for s in new_sections:
        if s["key"] == "projects":
            s["order"] = 1
        elif s["key"] == "summary":
            s["order"] = 4
        elif s["key"] == "certifications":
            s["enabled"] = False

    r = client.patch(
        f"/api/v1/resume-templates/{dup['id']}",
        json={"config": {**dup["config"], "sections": new_sections}},
        headers=h,
    )
    assert r.status_code == 200
    got = r.json()

    ordered = [s["key"] for s in got["config"]["sections"]]
    assert ordered[0] == "header"
    assert ordered[1] == "projects"  # (8) reorder applied
    assert "certifications" in ordered
    cert_row = next(s for s in got["config"]["sections"] if s["key"] == "certifications")
    assert cert_row["enabled"] is False  # (9) hidden

    enabled_keys = [s["key"] for s in got["config"]["sections"] if s["enabled"]]
    assert "certifications" not in enabled_keys


# ── 10, 11 ────────────────────────────────────────────────────────────────

def test_users_cannot_reach_each_others_templates(client):
    ha = auth_headers(client, "userA@test.com")
    hb = auth_headers(client, "userB@test.com")

    # userA creates a private template
    r = client.post(
        "/api/v1/resume-templates",
        json={"name": "A only", "base_template": "modern"},
        headers=ha,
    )
    assert r.status_code == 201
    a_tpl = r.json()

    # (10) userB cannot read / update / delete it
    assert client.get(f"/api/v1/resume-templates/{a_tpl['id']}", headers=hb).status_code == 404
    assert client.patch(f"/api/v1/resume-templates/{a_tpl['id']}",
                        json={"name": "hijacked"}, headers=hb).status_code == 404
    assert client.delete(f"/api/v1/resume-templates/{a_tpl['id']}", headers=hb).status_code == 404
    assert client.post("/api/v1/resume-templates/default",
                       json={"template_id": a_tpl["id"]}, headers=hb).status_code == 404

    # userB cannot see it in their list either
    body = _list_templates(client, hb)
    assert not any(t["id"] == a_tpl["id"] for t in body["templates"])

    # (11) userB creates an application and tries to attach A's template
    app_b = _mk_app(client, hb, company="B Corp")
    r = client.patch(f"/api/v1/applications/{app_b}/template",
                     json={"template_id": a_tpl["id"]}, headers=hb)
    assert r.status_code == 404


# ── 12 ────────────────────────────────────────────────────────────────────

def test_delete_current_default_falls_back(client):
    h = auth_headers(client, "u-del@test.com")

    # Create + set a user-owned default
    r = client.post(
        "/api/v1/resume-templates",
        json={"name": "Custom Modern", "base_template": "modern"},
        headers=h,
    )
    assert r.status_code == 201
    custom = r.json()

    r = client.post("/api/v1/resume-templates/default",
                    json={"template_id": custom["id"]}, headers=h)
    assert r.status_code == 200

    # Attach it to an application, then delete it.
    app_id = _mk_app(client, h)
    r = client.patch(f"/api/v1/applications/{app_id}/template",
                     json={"template_id": custom["id"]}, headers=h)
    assert r.status_code == 200

    r = client.delete(f"/api/v1/resume-templates/{custom['id']}", headers=h)
    assert r.status_code == 204

    # Default cleared, application falls back
    body = _list_templates(client, h)
    assert body["default_template_id"] is None
    r = client.get(f"/api/v1/applications/{app_id}/template", headers=h)
    assert r.status_code == 200
    resolved = r.json()["resolved"]
    assert resolved["source"] == "classic_fallback"
    assert resolved["base_template"] == "classic"

    # Exports still work
    assert client.get(f"/api/v1/applications/{app_id}/export/pdf", headers=h).status_code == 200
    assert client.get(f"/api/v1/applications/{app_id}/export/docx", headers=h).status_code == 200


# ── System templates are read-only ────────────────────────────────────────

def test_system_templates_are_read_only(client):
    h = auth_headers(client, "u-sys@test.com")
    body = _list_templates(client, h)
    system = _template_by_base(body, "classic")

    r = client.patch(f"/api/v1/resume-templates/{system['id']}",
                     json={"name": "hijack"}, headers=h)
    assert r.status_code == 403

    r = client.delete(f"/api/v1/resume-templates/{system['id']}", headers=h)
    assert r.status_code == 403

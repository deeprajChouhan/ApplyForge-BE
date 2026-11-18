"""Operator console: users list, audit trail, facets (admin overhaul, Nov 2026)."""
from app.models.enums import UserRole
from app.models.models import AuditLog, User
from tests.conftest import TestingSessionLocal, auth_headers

BASE = "/api/v1/admin"


def _admin_headers(client):
    headers = auth_headers(client, email="admin@example.com")
    with TestingSessionLocal() as db:
        u = db.query(User).filter(User.email == "admin@example.com").one()
        u.role = UserRole.admin
        db.commit()
    return headers


def _uid(email: str) -> int:
    with TestingSessionLocal() as db:
        return db.query(User.id).filter(User.email == email).scalar()


def _actions() -> list[str]:
    with TestingSessionLocal() as db:
        return [a for (a,) in db.query(AuditLog.action).order_by(AuditLog.id).all()]


def test_users_list_paginates_with_total_header(client):
    h = _admin_headers(client)
    for i in range(5):
        auth_headers(client, email=f"u{i}@example.com")

    r = client.get(f"{BASE}/users", headers=h, params={"page_size": 2, "page": 1})
    assert r.status_code == 200, r.text
    with TestingSessionLocal() as db:
        expected = db.query(User).count()  # includes any startup-seeded admin
    assert r.headers["x-total-count"] == str(expected)
    assert len(r.json()) == 2

    r3 = client.get(f"{BASE}/users", headers=h, params={"page_size": 2, "page": 3})
    assert len(r3.json()) == 2
    seen = {u["id"] for u in r.json()} | {u["id"] for u in r3.json()}
    assert len(seen) == 4  # pages don't overlap


def test_users_search_status_sort_and_new_fields(client):
    h = _admin_headers(client)
    auth_headers(client, email="alice@example.com")
    auth_headers(client, email="bob@example.com")
    bob = _uid("bob@example.com")
    client.post(f"{BASE}/users/{bob}/deactivate", headers=h)

    r = client.get(f"{BASE}/users", headers=h, params={"search": "ali"})
    assert [u["email"] for u in r.json()] == ["alice@example.com"]
    assert "last_seen_at" in r.json()[0] and r.json()[0]["last_seen_at"] is not None

    r = client.get(f"{BASE}/users", headers=h, params={"search": f"#{bob}"})
    assert [u["id"] for u in r.json()] == [bob]

    r = client.get(f"{BASE}/users", headers=h, params={"status": "disabled"})
    assert [u["email"] for u in r.json()] == ["bob@example.com"]

    r = client.get(f"{BASE}/users", headers=h, params={"sort": "email"})
    emails = [u["email"] for u in r.json()]
    assert emails == sorted(emails)

    for sort in ("oldest", "tokens", "last_seen"):
        assert client.get(f"{BASE}/users", headers=h, params={"sort": sort}).status_code == 200
    assert client.get(f"{BASE}/users", headers=h, params={"sort": "bogus"}).status_code == 422


def test_admin_user_mutations_are_audited(client):
    h = _admin_headers(client)
    auth_headers(client, email="target@example.com")
    uid = _uid("target@example.com")

    assert client.patch(f"{BASE}/users/{uid}", headers=h, json={"plan": "pro"}).status_code == 200
    assert client.post(f"{BASE}/users/{uid}/features", headers=h,
                       json={"feature": "chat", "enabled": False}).status_code == 200
    assert client.post(f"{BASE}/users/{uid}/deactivate", headers=h).status_code == 200
    assert client.post(f"{BASE}/users/{uid}/activate", headers=h).status_code == 200
    assert client.delete(f"{BASE}/users/{uid}", headers=h).status_code == 204

    assert _actions() == [
        "admin.user.update",
        "admin.user.feature_toggle",
        "admin.user.deactivate",
        "admin.user.activate",
        "admin.user.delete",
    ]
    r = client.get(f"{BASE}/audit-logs", headers=h, params={"page_size": 2})
    assert r.headers["x-total-count"] == "5"
    first = client.get(f"{BASE}/audit-logs", headers=h, params={"action": "admin.user.update"}).json()[0]
    assert first["before"]["plan"] == "free"
    assert first["after"]["plan"] == "pro"
    assert first["actor_user_email"] == "admin@example.com"

    facets = client.get(f"{BASE}/audit-logs/facets", headers=h).json()
    assert "admin.user.delete" in facets["actions"] and "user" in facets["entity_types"]


def test_noop_updates_are_not_audited(client):
    h = _admin_headers(client)
    auth_headers(client, email="same@example.com")
    uid = _uid("same@example.com")
    client.post(f"{BASE}/users/{uid}/activate", headers=h)  # already active
    client.patch(f"{BASE}/users/{uid}", headers=h, json={"plan": "free", "token_budget_monthly": 50000})
    assert "admin.user.activate" not in _actions()


def test_admin_cannot_lock_themselves_out(client):
    h = _admin_headers(client)
    me = _uid("admin@example.com")
    assert client.post(f"{BASE}/users/{me}/deactivate", headers=h).status_code == 400
    assert client.patch(f"{BASE}/users/{me}", headers=h, json={"role": "user"}).status_code == 400
    assert client.patch(f"{BASE}/users/{me}", headers=h, json={"is_active": False}).status_code == 400


def test_events_total_and_facets(client):
    h = _admin_headers(client)
    r = client.get(f"{BASE}/events", headers=h)
    assert r.status_code == 200 and "x-total-count" in r.headers
    f = client.get(f"{BASE}/events/facets", headers=h).json()
    assert set(f) == {"event_names", "entity_types"}


def test_usage_endpoint_portable_month_filter(client):
    h = _admin_headers(client)
    r = client.get(f"{BASE}/usage", headers=h, params={"month": "2026-12"})
    assert r.status_code == 200, r.text
    assert r.json()["new_users_this_month"] == 0


def test_recruiter_admin_actions_are_audited(client):
    h = _admin_headers(client)
    R = "/api/v1/recruiter/admin"
    agency = client.post(f"{R}/agencies", headers=h, json={"name": "Audit Co", "slug": "audit-co"})
    assert agency.status_code == 201, agency.text
    aid = agency.json()["id"]
    assert client.patch(f"{R}/agencies/{aid}", headers=h, json={"plan": "pro"}).status_code == 200
    rec = client.post(f"{R}/recruiters", headers=h, json={
        "agency_id": aid, "email": "r1@audit.co", "full_name": "R One", "password": "recruiterpass", "role": "owner"})
    assert rec.status_code == 201, rec.text
    rid = rec.json()["id"]
    assert client.post(f"{R}/recruiters/{rid}/reset-password", headers=h,
                       json={"password": "newpassword1"}).status_code == 200
    summary = client.get(f"{R}/billing/summary", headers=h).json()
    assert "trials_ending_soon" in summary

    acts = _actions()
    for a in ("admin.agency.create", "admin.agency.plan_update", "admin.recruiter.create",
              "admin.recruiter.reset_password"):
        assert a in acts, acts
    with TestingSessionLocal() as db:
        row = db.query(AuditLog).filter(AuditLog.action == "admin.recruiter.reset_password").one()
        assert "newpassword1" not in (row.extra_metadata or "") + (row.after or "")


# ── Overview / costs / quality / bulk flags ───────────────────────────────────

from datetime import datetime, timedelta  # noqa: E402

from app.models.enums import DocumentType, FeatureFlag, SubscriptionStatus  # noqa: E402
from app.models.evaluation_models import ApplicationEvaluation  # noqa: E402
from app.models.models import (  # noqa: E402
    GeneratedDocument,
    JobApplication,
    ParsedResumeData,
    SupportTicket,
    SupportTicketMessage,
    UsageEvent,
    UsageLedger,
    UserFeature,
)


def _seed_activity():
    """Two users: one heavy (over budget, past due, low-confidence parse, open ticket)."""
    now = datetime.utcnow()
    with TestingSessionLocal() as db:
        heavy = db.query(User).filter(User.email == "heavy@example.com").one()
        light = db.query(User).filter(User.email == "light@example.com").one()
        heavy.subscription_status = SubscriptionStatus.past_due
        db.add(UsageLedger(user_id=heavy.id, month_year=now.strftime("%Y-%m"),
                           tokens_used=heavy.token_budget_monthly, api_calls=10))
        db.add(UsageEvent(user_id=heavy.id, feature=FeatureFlag.jd_analyze, endpoint="/x",
                          tokens_in=100_000, tokens_out=50_000, model="gpt-4o-mini", created_at=now))
        db.add(UsageEvent(user_id=light.id, feature=FeatureFlag.chat, endpoint="/y",
                          tokens_in=1_000, tokens_out=1_000, model="gpt-4o", created_at=now))
        db.add(UsageEvent(user_id=light.id, feature=FeatureFlag.chat, endpoint="/y",
                          tokens_in=500, tokens_out=500, model="some-new-model", created_at=now))
        db.add(ParsedResumeData(user_id=heavy.id, raw_text="Python developer at Acme 2021-2024",
                                structured_json="{}", confidence_score=0.4))
        t = SupportTicket(user_id=heavy.id, subject="Help me", priority="high", status="open")
        db.add(t)
        db.flush()
        db.add(SupportTicketMessage(ticket_id=t.id, sender_user_id=heavy.id, sender_role="user", body="hi"))
        answered = SupportTicket(user_id=light.id, subject="Answered", status="open")
        db.add(answered)
        db.flush()
        db.add(SupportTicketMessage(ticket_id=answered.id, sender_user_id=light.id, sender_role="user", body="q",
                                    created_at=now - timedelta(minutes=5)))
        db.add(SupportTicketMessage(ticket_id=answered.id, sender_user_id=heavy.id, sender_role="admin", body="a"))
        app_ = JobApplication(user_id=heavy.id, company_name="Acme", role_title="Engineer", job_description="jd")
        db.add(app_)
        db.flush()
        db.add(GeneratedDocument(
            user_id=heavy.id, application_id=app_.id, doc_type=DocumentType.cover_letter,
            content=("I have 15+ years of experience. I am an AWS Certified Solutions Architect "
                     "and I reduced costs by 73% at Globex."),
        ))
        db.commit()


def test_overview_kpis_trend_and_attention(client):
    h = _admin_headers(client)
    auth_headers(client, email="heavy@example.com")
    auth_headers(client, email="light@example.com")
    _seed_activity()

    r = client.get(f"{BASE}/overview", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kpis"]["signups"]["current"] >= 3
    assert d["kpis"]["active_users"]["current"] == 2
    assert d["kpis"]["cost_usd"]["current"] > 0
    assert len(d["trend"]) == 30 and d["trend"][-1]["active_users"] == 2

    att = d["attention"]
    assert att["tickets_awaiting_reply"]["count"] == 1  # the answered ticket is excluded
    assert att["tickets_awaiting_reply"]["urgent"] == 1
    assert att["tickets_awaiting_reply"]["items"][0]["subject"] == "Help me"
    assert att["users_near_budget"]["count"] == 1 and att["users_near_budget"]["over_budget"] == 1
    assert att["users_near_budget"]["items"][0]["pct"] == 100
    assert att["past_due_subscriptions"]["count"] == 1
    assert att["low_confidence_parses"]["items"][0]["email"] == "heavy@example.com"


def test_costs_breakdown(client):
    h = _admin_headers(client)
    auth_headers(client, email="heavy@example.com")
    auth_headers(client, email="light@example.com")
    _seed_activity()

    r = client.get(f"{BASE}/costs", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    # gpt-4o-mini: 100k in @0.00015 + 50k out @0.0006 = 0.015 + 0.03 = 0.045
    # gpt-4o: 1k in @0.005 + 1k out @0.015 = 0.02 ; unknown: 1k total @0.002 = 0.002
    assert abs(d["total"]["cost_usd"] - 0.067) < 1e-6
    assert d["by_model"][0]["model"] == "gpt-4o-mini"
    assert {f["feature"] for f in d["by_feature"]} == {"jd_analyze", "chat"}
    assert d["top_users"][0]["email"] == "heavy@example.com"
    assert d["unpriced_models"] == ["some-new-model"]
    assert d["is_current_month"] is True and d["projected_month_end_usd"] is not None
    assert d["by_day"][-1]["cost_usd"] > 0
    assert d["active_users"] == 2

    assert client.get(f"{BASE}/costs", headers=h, params={"month": "2026-13"}).status_code == 422
    past = client.get(f"{BASE}/costs", headers=h, params={"month": "2020-01"}).json()
    assert past["total"]["cost_usd"] == 0 and past["projected_month_end_usd"] is None


def test_quality_spot_check_flags_ungrounded_claims(client):
    h = _admin_headers(client)
    auth_headers(client, email="heavy@example.com")
    auth_headers(client, email="light@example.com")
    _seed_activity()

    before = client.get(f"{BASE}/quality", headers=h).json()
    assert before["hallucination"]["evaluated_docs"] == 0
    assert before["generation"]["documents_generated"] == 1
    assert before["parsing"]["low_confidence"] == 1

    r = client.post(f"{BASE}/quality/spot-check", headers=h, json={"sample_size": 10})
    assert r.status_code == 200, r.text
    assert r.json()["checked"] == 1 and r.json()["flagged"] == 1

    after = client.get(f"{BASE}/quality", headers=h).json()["hallucination"]
    assert after["evaluated_docs"] == 1 and after["flagged_docs"] == 1 and after["flag_rate"] == 1.0
    assert after["recent_flagged"][0]["doc_type"] == "cover_letter"
    assert after["recent_flagged"][0]["flags"]
    assert "admin.quality.spot_check" in _actions()

    # Re-running upserts rather than duplicating
    client.post(f"{BASE}/quality/spot-check", headers=h, json={"sample_size": 10})
    with TestingSessionLocal() as db:
        assert db.query(ApplicationEvaluation).count() == 1


def test_feature_flags_bulk_preview_apply_and_coverage(client):
    h = _admin_headers(client)
    for e in ("f1@example.com", "f2@example.com", "f3@example.com"):
        auth_headers(client, email=e)

    def chat_rows():
        with TestingSessionLocal() as db:
            return db.query(UserFeature).filter(UserFeature.feature == FeatureFlag.chat).count()

    rows_before = chat_rows()
    preview = client.post(f"{BASE}/feature-flags/bulk", headers=h,
                          json={"feature": "chat", "enabled": True, "plan": "free", "dry_run": True})
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert p["dry_run"] is True and p["changed"] == p["matched_users"] and p["changed"] >= 3
    assert chat_rows() == rows_before  # dry run wrote nothing
    assert "admin.feature.bulk_set" not in _actions()

    applied = client.post(f"{BASE}/feature-flags/bulk", headers=h,
                          json={"feature": "chat", "enabled": True, "plan": "free"}).json()
    assert applied["changed"] == p["changed"]
    again = client.post(f"{BASE}/feature-flags/bulk", headers=h,
                        json={"feature": "chat", "enabled": True, "plan": "free"}).json()
    assert again["changed"] == 0  # idempotent
    assert _actions().count("admin.feature.bulk_set") == 1

    flags = client.get(f"{BASE}/feature-flags", headers=h).json()
    chat = next(s for s in flags["summary"] if s["feature"] == "chat")
    assert chat["enabled_by_plan"]["free"] == flags["plan_users"]["free"]

    filtered = client.get(f"{BASE}/feature-flags", headers=h, params={"feature": "chat", "search": "f2@"}).json()
    assert filtered["overrides_total"] == 1 and filtered["overrides"][0]["user_email"] == "f2@example.com"

    assert client.post(f"{BASE}/feature-flags/bulk", headers=h,
                       json={"feature": "chat", "enabled": False}).status_code == 400


def test_insight_endpoints_require_admin(client):
    h = auth_headers(client, email="plain@example.com")
    for path in ("/overview", "/costs", "/quality"):
        assert client.get(f"{BASE}{path}", headers=h).status_code == 403
    assert client.post(f"{BASE}/quality/spot-check", headers=h, json={}).status_code == 403
    assert client.post(f"{BASE}/feature-flags/bulk", headers=h,
                       json={"feature": "chat", "enabled": True, "plan": "free"}).status_code == 403

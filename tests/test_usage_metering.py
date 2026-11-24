"""Central AI metering, rolling 24h quotas, admin reset and user insights (Nov 2026)."""
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.enums import ApplicationStatus, PlanTier, UserRole
from app.models.models import (
    AuditLog, GeneratedDocument, JobApplication, PLAN_DAILY_TOKEN_LIMITS, ProductEvent, UsageEvent, UsageLedger, User,
)
from app.models.enums import DocumentType
from app.services.ai.factory import get_embedding_provider, get_llm_provider
from app.services.usage import metering
from app.services.usage.metering import UsageContextMiddleware, endpoint_label, usage_scope
from app.services.usage.quota import QuotaService, UsageLimitExceeded
from tests.conftest import TestingSessionLocal, auth_headers

ADMIN = "/api/v1/admin"


def _admin(client):
    h = auth_headers(client, email="ops@example.com")
    with TestingSessionLocal() as db:
        db.query(User).filter_by(email="ops@example.com").one().role = UserRole.admin
        db.commit()
    return h


def _uid(email):
    with TestingSessionLocal() as db:
        return db.query(User.id).filter_by(email=email).scalar()


def _spend(user_id, tokens, when=None, kind="llm", endpoint="POST /x"):
    with TestingSessionLocal() as db:
        db.add(UsageEvent(user_id=user_id, endpoint=endpoint, kind=kind, tokens_in=tokens, tokens_out=0,
                          model="gpt-4o-mini", created_at=when or datetime.utcnow()))
        db.commit()


def test_endpoint_label_templates_ids():
    assert endpoint_label("post", "/api/v1/applications/42/tailor") == "POST /applications/{id}/tailor"
    assert endpoint_label("GET", "/api/v1/chat/threads/7/messages?x=1") == "GET /chat/threads/{id}/messages"


def test_llm_call_in_scope_is_recorded_with_ledger(client):
    auth_headers(client, email="m@example.com")
    uid = _uid("m@example.com")
    with usage_scope(uid, "crawler.scheduled"):
        get_llm_provider().generate("system", "hello world " * 20)
    with TestingSessionLocal() as db:
        ev = db.query(UsageEvent).filter_by(user_id=uid).one()
        assert (ev.endpoint, ev.kind, ev.model) == ("crawler.scheduled", "llm", "mock")
        assert ev.tokens_in > 0 and ev.tokens_out > 0
        assert db.query(UsageLedger).filter_by(user_id=uid).one().tokens_used == ev.tokens_in + ev.tokens_out


def test_calls_without_context_are_kept_as_system_spend(client):
    get_llm_provider().generate("s", "anonymous call")
    with TestingSessionLocal() as db:
        ev = db.query(UsageEvent).one()
        assert ev.user_id is None and ev.endpoint == "system"


def test_embeddings_are_batched_and_not_counted_toward_quota(client):
    auth_headers(client, email="e@example.com")
    uid = _uid("e@example.com")
    with usage_scope(uid, "POST /files/{id}/index"):
        for i in range(5):
            get_embedding_provider().embed(f"chunk {i} of a resume")
    with TestingSessionLocal() as db:
        rows = db.query(UsageEvent).filter_by(user_id=uid).all()
        assert len(rows) == 1 and rows[0].kind == "embedding"
        assert rows[0].endpoint.endswith("×5")
        user = db.get(User, uid)
        assert QuotaService(db).daily(user).used == 0


def test_middleware_propagates_to_threadpool_and_flags_swallowed_limits(client):
    """A service that swallows the 429 still lets the UI know AI was skipped."""
    auth_headers(client, email="s@example.com")
    uid = _uid("s@example.com")
    with TestingSessionLocal() as db:
        db.get(User, uid).token_budget_daily = 10
        db.commit()
    _spend(uid, 50)

    mini = FastAPI()
    mini.add_middleware(UsageContextMiddleware)

    @mini.post("/api/v1/applications/{app_id}/analyze")
    def analyze(app_id: int):  # sync → runs in the threadpool
        ctx = metering.current()
        try:
            get_llm_provider().generate("s", "u")
            used_ai = True
        except UsageLimitExceeded:
            used_ai = False  # heuristic fallback, like several real services
        return {"user_id": ctx.user_id, "endpoint": ctx.endpoint, "used_ai": used_ai}

    token = client.post("/api/v1/auth/login", json={"email": "s@example.com", "password": "password123"}).json()["access_token"]
    r = TestClient(mini).post("/api/v1/applications/9/analyze", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"user_id": uid, "endpoint": "POST /applications/{id}/analyze", "used_ai": False}
    assert r.headers["x-usage-limited"] == "daily"


def test_daily_limit_blocks_then_admin_reset_restores(client):
    h_admin = _admin(client)
    auth_headers(client, email="d@example.com")
    uid = _uid("d@example.com")
    with TestingSessionLocal() as db:
        db.get(User, uid).token_budget_daily = 100
        db.commit()
    _spend(uid, 60, when=datetime.utcnow() - timedelta(hours=30))  # outside the window
    _spend(uid, 120, when=datetime.utcnow() - timedelta(hours=2))

    with TestingSessionLocal() as db:
        user = db.get(User, uid)
        d = QuotaService(db).daily(user)
        assert (d.used, d.limit, d.exhausted) == (120, 100, True)
        assert d.resets_at is not None and d.resets_at > datetime.utcnow()
        try:
            QuotaService(db).enforce(uid)
            raise AssertionError("should be blocked")
        except UsageLimitExceeded as exc:
            assert exc.detail["error"] == "daily_token_limit_exceeded"

    me = client.post("/api/v1/auth/login", json={"email": "d@example.com", "password": "password123"}).json()["access_token"]
    s = client.get("/api/v1/usage/me", headers={"Authorization": f"Bearer {me}"}).json()
    assert s["blocked"] == "daily" and s["daily"]["pct"] == 120.0 and s["monthly"]["limit"] > 0

    r = client.post(f"{ADMIN}/users/{uid}/usage/reset-daily", headers=h_admin)
    assert r.status_code == 200, r.text
    assert r.json()["daily"]["used"] == 0 and r.json()["blocked"] is None
    with TestingSessionLocal() as db:
        QuotaService(db).enforce(uid)  # no raise
        assert db.query(AuditLog).filter_by(action="admin.user.usage_reset_daily").count() == 1


def test_monthly_budget_blocks_any_metered_call(client):
    auth_headers(client, email="mo@example.com")
    uid = _uid("mo@example.com")
    with TestingSessionLocal() as db:
        db.get(User, uid).token_budget_monthly = 5
        db.commit()
    with usage_scope(uid, "x"):
        get_llm_provider().generate("s", "first call " * 10)  # spends > 5
        try:
            get_llm_provider().generate("s", "second")
            raise AssertionError("should be blocked")
        except UsageLimitExceeded as exc:
            assert exc.detail["error"] == "token_budget_exceeded"


def test_admin_can_override_and_clear_daily_limit(client):
    h = _admin(client)
    auth_headers(client, email="o@example.com")
    uid = _uid("o@example.com")
    r = client.patch(f"{ADMIN}/users/{uid}", json={"token_budget_daily": 1234}, headers=h)
    assert r.json()["token_budget_daily"] == 1234 and r.json()["daily_limit_is_custom"] is True
    r = client.patch(f"{ADMIN}/users/{uid}", json={"token_budget_daily": None}, headers=h)
    assert r.json()["token_budget_daily"] == PLAN_DAILY_TOKEN_LIMITS[PlanTier.free]
    assert r.json()["daily_limit_is_custom"] is False


def test_admins_are_exempt(client):
    _admin(client)
    uid = _uid("ops@example.com")
    with TestingSessionLocal() as db:
        db.get(User, uid).token_budget_daily = 1
        db.commit()
    _spend(uid, 999)
    with TestingSessionLocal() as db:
        QuotaService(db).enforce(uid)


def test_user_insights_roles_sites_docs_and_cost(client):
    h = _admin(client)
    auth_headers(client, email="i@example.com")
    uid = _uid("i@example.com")
    with TestingSessionLocal() as db:
        rows = [
            ("Applied AI Engineer", "Gresearch", "https://www.linkedin.com/jobs/view/1", ApplicationStatus.applied),
            ("AI Engineer | EdTech", "Techmunity", "https://boards.greenhouse.io/x/1", ApplicationStatus.interview),
            ("Senior Backend Engineer", "Revolut", None, ApplicationStatus.draft),
        ]
        for title, co, link, st in rows:
            db.add(JobApplication(user_id=uid, company_name=co, role_title=title, job_description="jd",
                                  jd_link=link, status=st, priority_score=70))
        db.flush()
        app_id = db.query(JobApplication.id).filter_by(user_id=uid).first()[0]
        db.add(GeneratedDocument(user_id=uid, application_id=app_id, doc_type=DocumentType.cover_letter, version=1, content="c"))
        db.add(ProductEvent(user_id=uid, event_name="extension_job_scored", properties='{"source_site": "indeed"}'))
        db.commit()
    _spend(uid, 2000, endpoint="POST /applications/{id}/tailor")

    r = client.get(f"{ADMIN}/users/{uid}/insights", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    s = d["search"]
    assert {x["label"] for x in s["sites"]} == {"LinkedIn", "Greenhouse", "No link (pasted JD)"}
    assert s["extension_sites"] == [{"label": "Indeed", "count": 1}]
    assert s["role_families"][0] == {"label": "AI / ML", "count": 2}
    assert s["funnel"] == {"saved": 3, "applied": 2, "interview": 1, "offer": 0}
    assert d["documents"]["generated_by_type"] == {"cover_letter": 1}
    assert d["by_operation"][0]["operation"] == "POST /applications/{id}/tailor"
    assert d["spend"]["month_tokens"] == 2000 and d["spend"]["month_cost_usd"] > 0
    assert len(d["daily_usage"]) == 30
    assert d["quota"]["daily"]["used"] == 2000


def test_costs_include_operations_and_anonymous_spend(client):
    h = _admin(client)
    auth_headers(client, email="c@example.com")
    _spend(_uid("c@example.com"), 1000, endpoint="POST /applications/{id}/tailor")
    _spend(None, 500, endpoint="POST /ephemeral/analyze")
    r = client.get(f"{ADMIN}/costs", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    ops = {o["operation"] for o in d["by_operation"]}
    assert {"POST /applications/{id}/tailor", "POST /ephemeral/analyze"} <= ops
    assert any(u["user_id"] is None and u["email"] == "Anonymous / system" for u in d["top_users"])
    assert d["active_users"] == 1

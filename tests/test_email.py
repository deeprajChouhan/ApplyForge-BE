"""
Email system (Resend) — offline: console provider, sync dispatch, in-memory SQLite.

Covers: rendering + escaping, svix signatures, built-in sends (welcome, password
reset/changed, support reply), preferences + unsubscribe, admin automations,
webhook delivery/bounce/suppression, inbound mailbox + help-desk mirroring,
recruiter notification emails + muting, and the data wall (no cross FKs).
"""
import json
import os
import tempfile
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("EMBEDDING_PROVIDER", "mock")
os.environ.setdefault("UPLOAD_DIR", os.path.join(tempfile.gettempdir(), f"email_pytest_{os.getpid()}"))

from app.core.config import settings  # noqa: E402
from app.core.security import create_token, hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.email import (  # noqa: E402
    EmailMessage, EmailPreference, OpsEmailRule, OpsEmailSuppression, OpsMailboxMessage, OpsMailboxThread,
)
from app.models.enums import UserRole  # noqa: E402
from app.models.models import SupportTicket, SupportTicketMessage, User  # noqa: E402
from app.services.email import svix  # noqa: E402
from app.services.email.provider import ConsoleEmailProvider, get_email_provider  # noqa: E402
from app.services.email.render import render  # noqa: E402
from app.services.email.themes import CONSUMER  # noqa: E402

API = "/api/v1"
SECRET = "whsec_" + "dGVzdHNlY3JldHRlc3RzZWNyZXR0ZXN0c2VjcmV0MTI="


@pytest.fixture(autouse=True)
def setup_db():
    """Fresh in-memory DB per test (overrides conftest's file-based one)."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    old = {k: getattr(settings, k) for k in ("email_jobs_backend", "email_provider", "resend_webhook_secret")}
    settings.email_jobs_backend = "sync"
    settings.email_provider = "console"
    settings.resend_webhook_secret = SecretStr(SECRET)
    get_email_provider.cache_clear()
    ConsoleEmailProvider.sent.clear()
    app.dependency_overrides[get_db] = _get_db
    pytest.Session = Session
    yield
    app.dependency_overrides.pop(get_db, None)
    for k, v in old.items():
        setattr(settings, k, v)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def db():
    return pytest.Session()


def register(client, email="cand@example.com", password="password123"):
    client.post(f"{API}/auth/register", json={"email": email, "password": password})
    tok = client.post(f"{API}/auth/login", json={"email": email, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def admin(client):
    s = db()
    u = User(email="ops@example.com", password_hash=hash_password("x" * 10), role=UserRole.admin)
    s.add(u)
    s.commit()
    tok = create_token(str(u.id), "access", timedelta(minutes=30))
    s.close()
    return {"Authorization": f"Bearer {tok}"}


def sent_to(addr):
    return [m for m in ConsoleEmailProvider.sent if addr in m.to]


def signed_post(client, payload: dict, svix_id: str | None = None):
    body = json.dumps(payload).encode()
    sid, ts = svix_id or f"msg_{time.time_ns()}", str(int(time.time()))
    return client.post(f"{API}/webhooks/resend", content=body, headers={
        "svix-id": sid, "svix-timestamp": ts, "svix-signature": svix.sign(SECRET, sid, ts, body),
        "content-type": "application/json"})


# ── rendering ──────────────────────────────────────────────────────────────
def test_render_escapes_user_values_and_supports_markdown():
    r = render(theme=CONSUMER, subject="Hi {{ name }}", body="Hello **{{ name }}**\n\n- one\n- two",
               ctx={"name": "<script>alert(1)</script>"}, cta_label="Go", cta_url="https://x.test/a")
    assert "<script>" not in r.html
    assert "&lt;script&gt;" in r.html
    assert "<strong>" in r.html and "<ul" in r.html
    assert r.subject == "Hi <script>alert(1)</script>"  # subject is plain text, not HTML
    assert "Go: https://x.test/a" in r.text


def test_sandbox_blocks_unsafe_template_access():
    from jinja2.exceptions import SecurityError
    with pytest.raises(SecurityError):
        render(theme=CONSUMER, subject="x", body="{{ ''.__class__.__mro__[1].__subclasses__() }}", ctx={})


def test_svix_signature_roundtrip_and_rejects_tamper():
    body = b'{"a":1}'
    ts = str(int(time.time()))
    sig = svix.sign(SECRET, "id1", ts, body)
    svix.verify(SECRET, svix_id="id1", svix_timestamp=ts, svix_signature=sig, body=body)
    with pytest.raises(svix.WebhookVerificationError):
        svix.verify(SECRET, svix_id="id1", svix_timestamp=ts, svix_signature=sig, body=b'{"a":2}')
    with pytest.raises(svix.WebhookVerificationError):
        svix.verify(SECRET, svix_id="id1", svix_timestamp=str(int(time.time()) - 3600), svix_signature=sig, body=body)


# ── built-in sends ─────────────────────────────────────────────────────────
def test_register_sends_welcome_once(client):
    register(client)
    assert len(sent_to("cand@example.com")) == 1
    m = sent_to("cand@example.com")[0]
    assert "Welcome" in m.subject and "#7c3aed" in m.html  # candidate theme
    s = db()
    row = s.query(EmailMessage).one()
    assert row.status == "sent" and row.template_key == "c_welcome" and row.category == "transactional"
    s.close()


def test_forgot_and_reset_password_single_use(client):
    register(client)
    ConsoleEmailProvider.sent.clear()
    assert client.post(f"{API}/auth/forgot-password", json={"email": "nobody@example.com"}).status_code == 200
    assert ConsoleEmailProvider.sent == []  # no enumeration, nothing sent
    client.post(f"{API}/auth/forgot-password", json={"email": "cand@example.com"})
    mail = sent_to("cand@example.com")[0]
    token = mail.text.split("reset-password?token=")[1].split()[0].strip(")")
    r = client.post(f"{API}/auth/reset-password", json={"token": token, "password": "newpassword1"})
    assert r.status_code == 200, r.text
    assert client.post(f"{API}/auth/login", json={"email": "cand@example.com", "password": "newpassword1"}).status_code == 200
    # Same link again → rejected (password hash changed)
    assert client.post(f"{API}/auth/reset-password", json={"token": token, "password": "another123"}).status_code == 400
    assert any(m.subject.startswith("Your ApplyForge password was changed") for m in sent_to("cand@example.com"))


def test_preferences_and_unsubscribe(client):
    h = register(client)
    r = client.get(f"{API}/email/preferences", headers=h).json()
    assert r == {"notifications": True, "digests": True, "product_updates": True}  # soft opt-in defaults
    client.put(f"{API}/email/preferences", headers=h, json={"digests": False})
    assert client.get(f"{API}/email/preferences", headers=h).json()["digests"] is False

    from app.services.email.tokens import make_unsub_token
    s = db()
    uid = s.query(User.id).filter(User.email == "cand@example.com").scalar()
    s.close()
    tok = make_unsub_token("consumer", uid, "lifecycle")
    assert client.get(f"{API}/email/unsubscribe/info", params={"token": tok}).json()["category"] == "lifecycle"
    assert client.post(f"{API}/email/unsubscribe", params={"token": tok}).status_code == 200
    assert client.get(f"{API}/email/preferences", headers=h).json()["product_updates"] is False
    assert client.post(f"{API}/email/unsubscribe", params={"token": "junk"}).status_code == 400


# ── automations ────────────────────────────────────────────────────────────
def test_admin_rule_fires_on_first_application_and_respects_opt_out(client):
    ah = admin(client)
    rule = client.post(f"{API}/admin/email/rules", headers=ah, json={
        "name": "First role saved → extension nudge", "scope": "consumer", "event_key": "application.created",
        "conditions": {"match": "all", "rules": [{"field": "event.is_first", "op": "is_true"},
                                                 {"field": "facts.extension_connected", "op": "is_false"}]},
        "template_key": "c_nudge_extension", "is_active": True,
    })
    assert rule.status_code == 201, rule.text
    # wrong scope/template combos are rejected
    bad = client.post(f"{API}/admin/email/rules", headers=ah, json={
        "name": "x", "scope": "consumer", "event_key": "rec.notification.created", "template_key": "c_welcome"})
    assert bad.status_code == 422

    h = register(client)
    ConsoleEmailProvider.sent.clear()
    s = db()
    from app.models.models import JobApplication
    uid = s.query(User.id).filter(User.email == "cand@example.com").scalar()
    s.add(JobApplication(user_id=uid, company_name="Monzo", role_title="Backend Engineer", job_description="Python"))
    s.commit()
    s.add(JobApplication(user_id=uid, company_name="Wise", role_title="Platform Engineer", job_description="Go"))
    s.commit()
    s.close()
    nudges = [m for m in sent_to("cand@example.com") if "Priority Score" in m.subject]
    assert len(nudges) == 1  # only the first application, once per person
    assert "List-Unsubscribe" in nudges[0].headers  # lifecycle mail carries unsubscribe

    # opted out of product updates → a new matching user doesn't get it
    s = db()
    s.add(User(email="quiet@example.com", password_hash="x", is_active=True))
    s.commit()
    qid = s.query(User.id).filter(User.email == "quiet@example.com").scalar()
    s.add(EmailPreference(user_id=qid, notifications=True, digests=True, product_updates=False))
    s.commit()
    s.add(JobApplication(user_id=qid, company_name="A", role_title="B", job_description="C"))
    s.commit()
    s.close()
    assert not [m for m in sent_to("quiet@example.com") if "Priority Score" in m.subject]

    stats = client.get(f"{API}/admin/email/rules/{rule.json()['id']}/stats", headers=ah).json()
    assert stats["fire_count"] == 1


def test_daily_rule_dry_run_and_run(client):
    ah = admin(client)
    register(client, "noresume@example.com")
    r = client.post(f"{API}/admin/email/rules", headers=ah, json={
        "name": "No resume yet", "scope": "consumer", "event_key": "schedule.daily",
        "conditions": {"match": "all", "rules": [{"field": "facts.resume_count", "op": "eq", "value": 0}]},
        "template_key": "c_nudge_upload_resume", "is_active": True}).json()
    dry = client.post(f"{API}/admin/email/rules/{r['id']}/dry-run", headers=ah).json()
    assert dry["supported"] and "noresume@example.com" in dry["sample"]

    from app.services.email.rules import run_daily
    s = db()
    run_daily(s, "consumer")
    run_daily(s, "consumer")  # second day: once-per-person
    s.close()
    assert len([m for m in sent_to("noresume@example.com") if "resume" in m.subject.lower()]) == 1


# ── webhooks ───────────────────────────────────────────────────────────────
def test_webhook_delivery_bounce_and_suppression(client):
    register(client)
    s = db()
    row = s.query(EmailMessage).one()
    pid = row.provider_message_id
    s.close()
    assert signed_post(client, {"type": "email.delivered", "data": {"email_id": pid, "to": ["cand@example.com"]}}).status_code == 200
    s = db()
    assert s.query(EmailMessage).one().status == "delivered"
    s.close()
    signed_post(client, {"type": "email.bounced", "data": {"email_id": pid, "to": ["cand@example.com"],
                                                            "bounce": {"type": "Permanent", "message": "no mailbox"}}})
    s = db()
    assert s.query(EmailMessage).one().status == "bounced"
    assert s.query(OpsEmailSuppression).filter_by(email="cand@example.com").count() == 1
    s.close()
    # bad signature rejected; duplicate svix-id ignored
    assert client.post(f"{API}/webhooks/resend", content=b"{}", headers={"svix-id": "a", "svix-timestamp": "1", "svix-signature": "v1,x"}).status_code == 401
    assert signed_post(client, {"type": "email.opened", "data": {"email_id": pid}}, svix_id="dup").json().get("duplicate") is None
    assert signed_post(client, {"type": "email.opened", "data": {"email_id": pid}}, svix_id="dup").json()["duplicate"] is True

    # suppressed address: next email is recorded but not sent
    ConsoleEmailProvider.sent.clear()
    client.post(f"{API}/auth/forgot-password", json={"email": "cand@example.com"})
    assert ConsoleEmailProvider.sent == []


def test_inbound_support_mail_threads_and_mirrors_to_helpdesk(client):
    ah = admin(client)
    register(client)
    ConsoleEmailProvider.sent.clear()
    payload = {"type": "email.received", "data": {
        "email_id": "in-1", "from": "cand@example.com", "to": ["support@applyforge.co.uk"],
        "subject": "Extension not showing scores", "message_id": "<m1@example.com>", "attachments": []}}
    assert signed_post(client, payload).json()["result"] == "ingested"
    s = db()
    t = s.query(OpsMailboxThread).one()
    assert t.mailbox == "support" and t.unread and t.support_ticket_id
    ticket = s.get(SupportTicket, t.support_ticket_id)
    assert ticket.category == "email"
    s.close()

    # Reply from the mailbox: email goes out once, ticket gets the admin message,
    # and the help-desk "we replied" email is NOT sent on top.
    r = client.post(f"{API}/admin/email/mailbox/threads/{t.id}/reply", headers=ah, json={"body": "Fixed in 1.4.2."})
    assert r.status_code == 200, r.text
    out = sent_to("cand@example.com")
    assert len(out) == 1 and out[0].subject.startswith("Re: Extension not showing scores")
    assert out[0].headers.get("In-Reply-To") == "<m1@example.com>"
    s = db()
    msgs = s.query(SupportTicketMessage).filter_by(ticket_id=ticket.id).all()
    assert [m.sender_role for m in msgs] == ["user", "admin"]
    s.close()

    # Customer replies → same thread
    signed_post(client, {"type": "email.received", "data": {
        "email_id": "in-2", "from": "cand@example.com", "to": ["support@applyforge.co.uk"],
        "subject": "Re: Extension not showing scores", "message_id": "<m2@example.com>"}})
    s = db()
    assert s.query(OpsMailboxThread).count() == 1
    assert s.query(OpsMailboxMessage).count() == 3
    s.close()

    look = client.get(f"{API}/admin/email/mailbox/lookup", headers=ah, params={"email": "cand@example.com"}).json()
    assert look["consumer"]["user_id"] and look["recruiter"] is None


def test_helpdesk_admin_reply_emails_user(client):
    ah = admin(client)
    h = register(client)
    t = client.post(f"{API}/support/tickets", headers=h, json={"subject": "Billing", "category": "billing",
                                                              "priority": "normal", "message": "Charged twice"}).json()
    ConsoleEmailProvider.sent.clear()
    r = client.post(f"{API}/admin/support/tickets/{t['id']}/reply", headers=ah, json={"body": "Refunded."})
    assert r.status_code == 200, r.text
    out = sent_to("cand@example.com")
    assert len(out) == 1 and f"[#{t['id']}]" in out[0].subject


# ── templates ──────────────────────────────────────────────────────────────
def test_template_override_preview_and_reset(client):
    ah = admin(client)
    assert any(t["key"] == "r_invite" for t in client.get(f"{API}/admin/email/templates", headers=ah).json())
    bad = client.put(f"{API}/admin/email/templates/c_welcome", headers=ah, json={
        "name": "Welcome", "subject": "{% if %}", "body": "x"})
    assert bad.status_code == 422
    ok = client.put(f"{API}/admin/email/templates/c_welcome", headers=ah, json={
        "name": "Welcome", "subject": "Hey {{ first_name or 'friend' }}", "body": "Custom body"})
    assert ok.status_code == 200 and ok.json()["overridden"]
    p = client.post(f"{API}/admin/email/templates/preview", headers=ah, json={"key": "c_welcome"}).json()
    assert "Custom body" in p["html"]
    register(client)
    assert sent_to("cand@example.com")[0].subject == "Hey friend"
    assert client.delete(f"{API}/admin/email/templates/c_welcome", headers=ah).json()["reset"] is True


# ── recruiter ──────────────────────────────────────────────────────────────
def _agency_with_seat(s):
    from app.recruiter.enums import RecruiterSeatRole
    from app.recruiter.models import Agency, Recruiter
    a = Agency(name="Northbridge", slug="northbridge")
    s.add(a)
    s.flush()
    r = Recruiter(agency_id=a.id, email="sam@northbridge.co.uk", full_name="Sam Carter",
                  role=RecruiterSeatRole.owner, password_hash=hash_password("recruiterpass"))
    s.add(r)
    s.commit()
    return a, r


def test_recruiter_notification_email_and_muting(client):
    from app.recruiter.enums import NotificationKind
    from app.recruiter.models import Notification, RecEmailMessage
    s = db()
    a, r = _agency_with_seat(s)
    ConsoleEmailProvider.sent.clear()
    s.add(Notification(agency_id=a.id, recruiter_id=r.id, kind=NotificationKind.interview_upcoming,
                       title="Interview tomorrow: Ada × Monzo", body="10:00, video", parent_kind="interview"))
    s.commit()
    out = sent_to("sam@northbridge.co.uk")
    assert len(out) == 1 and out[0].subject == "Interview tomorrow: Ada × Monzo"
    assert "#4a6d50" in out[0].html  # recruiter (sage) theme
    # stale_pipeline is muted by default → in-app only
    s.add(Notification(agency_id=a.id, recruiter_id=r.id, kind=NotificationKind.stale_pipeline, title="Stale"))
    s.commit()
    assert len(sent_to("sam@northbridge.co.uk")) == 1
    assert s.query(RecEmailMessage).filter_by(template_key="r_notification").count() == 1
    s.close()


def test_recruiter_forgot_password_and_prefs(client):
    s = db()
    _agency_with_seat(s)
    s.close()
    ConsoleEmailProvider.sent.clear()
    client.post(f"{API}/recruiter/auth/forgot-password", json={"email": "sam@northbridge.co.uk"})
    mail = sent_to("sam@northbridge.co.uk")[0]
    token = mail.text.split("reset-password?token=")[1].split()[0].strip(")")
    assert client.post(f"{API}/recruiter/auth/reset-password", json={"token": token, "password": "brandnew123"}).status_code == 200
    tok = client.post(f"{API}/recruiter/auth/login", json={"email": "sam@northbridge.co.uk", "password": "brandnew123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    p = client.get(f"{API}/recruiter/me/email-preferences", headers=h).json()
    assert "stale_pipeline" in p["muted_kinds"]
    p = client.put(f"{API}/recruiter/me/email-preferences", headers=h, json={"muted_kinds": ["bogus", "offer_updated"]}).json()
    assert p["muted_kinds"] == ["offer_updated"]


def test_data_wall_no_cross_product_foreign_keys():
    consumer_tables = {"users"} | {t.name for t in Base.metadata.sorted_tables if not t.name.startswith(("rec_", "ops_"))}
    for t in Base.metadata.sorted_tables:
        targets = {fk.column.table.name for fk in t.foreign_keys}
        if t.name.startswith("rec_"):
            assert not (targets & consumer_tables), f"{t.name} → {targets & consumer_tables}"
        if t.name.startswith("ops_"):
            assert all(x.startswith("ops_") for x in targets), f"{t.name} → {targets}"

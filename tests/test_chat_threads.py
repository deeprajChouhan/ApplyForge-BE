"""Coach chat, Phase 4b: threads, context, SSE send, stop, intent routing, citations, edits, apply-edit."""
import json

import pytest

from app.models.enums import DocumentType, FeatureFlag
from app.models.models import (
    ApplicationChat,
    ApplicationChatMessage,
    GeneratedDocument,
    JobApplication,
    ParsedResumeData,
    UsageEvent,
    User,
    UserFeature,
)
from app.services.ai.exceptions import AIProviderTransientError
from app.services.ai.factory import get_llm_provider
from app.services.ai.providers import MockLLMProvider, StreamChunk
from app.services.chat import coach, stream as chat_stream
from app.services.rag.service import RAGService
from app.services.scoring.service import CompetitionScorer
from tests.conftest import TestingSessionLocal, auth_headers

JD = "Senior Backend Engineer. 5+ years with Python and PostgreSQL. Immediate start."
ANALYSIS = {"keywords": ["microservices"], "required_skills": ["Python"], "preferred_skills": [],
            "strengths": ["Python"], "unsupported_gaps": ["Kubernetes"], "fit_summary": "Good."}
COVER = "Dear team, I am applying for the role. I built payment services in Python. I look forward to hearing from you."


def _setup(client, email, with_cover=True):
    h = auth_headers(client, email)
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter_by(email=email).first()
        db.add(UserFeature(user_id=user.id, feature=FeatureFlag.chat, enabled=True))
        resume = ParsedResumeData(user_id=user.id, raw_text="Built payment services in Python at Ledgerly.",
                                  structured_json=json.dumps({"summary": "Built payment services in Python at Ledgerly."}),
                                  confidence_score=0.91)
        db.add(resume)
        db.flush()
        app = JobApplication(user_id=user.id, company_name="Northwind", role_title="Senior Backend Engineer",
                             job_description=JD, jd_analysis_json=json.dumps(ANALYSIS), selected_resume_id=resume.id,
                             priority_score=70.0)
        db.add(app)
        db.flush()
        if with_cover:
            db.add(GeneratedDocument(user_id=user.id, application_id=app.id, doc_type=DocumentType.cover_letter,
                                     version=1, content=COVER, format="txt"))
        db.commit()
        return h, user.id, app.id
    finally:
        db.close()


@pytest.fixture(autouse=True)
def fake_rag(monkeypatch):
    class _Chunk:
        def __init__(self, i, content):
            self.id, self.content = i, content

    def search(self, query, top_k=5, parsed_resume_id=None):
        return [(_Chunk(101, "Built payment services in Python at Ledgerly."), 0.9)]

    monkeypatch.setattr(RAGService, "search", search)
    monkeypatch.setattr(chat_stream, "STOP_POLL_SECONDS", 0)


def _stream_text(monkeypatch, pieces, usage=None, after_first=None, fail_after=None):
    provider = get_llm_provider()

    def fake_stream(system_prompt, user_prompt):
        for i, p in enumerate(pieces):
            if fail_after is not None and i == fail_after:
                raise AIProviderTransientError("boom")
            yield StreamChunk(text=p)
            if i == 0 and after_first:
                after_first()
        if usage:
            yield StreamChunk(usage=usage)

    # Patch the raw provider inside the usage meter so metering still runs.
    monkeypatch.setattr(getattr(provider, "inner", provider), "stream", fake_stream)


def _events(body: str):
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.split("\n") if ": " in l)
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def _thread(client, h, app_id):
    r = client.post(f"/api/v1/chat/{app_id}/threads", json={}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── provider ────────────────────────────────────────────────────────────

def test_mock_provider_streams_text_then_usage():
    chunks = list(MockLLMProvider().stream("sys", "hello world " * 10))
    assert "".join(c.text for c in chunks).startswith("[MOCK_GENERATION]")
    assert chunks[-1].usage and chunks[-1].usage["in"] > 0


# ── threads + context ─────────────────────────────────────────────────

def test_threads_crud_and_scoping(client):
    h, _, app_id = _setup(client, "threads@test.com")
    a = _thread(client, h, app_id)
    b = client.post(f"/api/v1/chat/{app_id}/threads", json={"title": "Interview prep"}, headers=h).json()
    listed = client.get(f"/api/v1/chat/{app_id}/threads", headers=h).json()
    assert {t["id"] for t in listed} == {a, b["id"]}
    r = client.patch(f"/api/v1/chat/threads/{a}", json={"title": "Cover letter", "archived": True}, headers=h)
    assert r.status_code == 200 and r.json()["title"] == "Cover letter"
    assert [t["id"] for t in client.get(f"/api/v1/chat/{app_id}/threads", headers=h).json()] == [b["id"]]

    other, _, _ = _setup(client, "threads-other@test.com")
    assert client.get(f"/api/v1/chat/{app_id}/threads", headers=other).status_code == 404
    assert client.patch(f"/api/v1/chat/threads/{a}", json={"title": "x"}, headers=other).status_code == 404
    assert client.get(f"/api/v1/chat/threads/{a}/messages", headers=other).status_code == 404


def test_context_endpoint(client):
    h, _, app_id = _setup(client, "ctx@test.com")
    c = client.get(f"/api/v1/chat/{app_id}/context", headers=h).json()
    assert c["resume"]["parse_confidence"] == pytest.approx(0.91)
    assert c["jd"] == {"present": True, "analysed": True}
    assert c["documents"] == [{"doc_type": "cover_letter", "version": 1}]
    assert c["priority_score"] == 70.0


# ── streaming send ──────────────────────────────────────────────────────

def test_stream_qa_events_citations_and_usage(client, monkeypatch):
    h, user_id, app_id = _setup(client, "sse@test.com")
    tid = _thread(client, h, app_id)
    _stream_text(monkeypatch, ["Lead with Ledgerly [E1]", " and ignore [E9].", ""], usage={"in": 900, "out": 40})
    r = client.post(f"/api/v1/chat/threads/{tid}/messages", json={"content": "What should I lead with?"}, headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    ev = _events(r.text)
    names = [e for e, _ in ev]
    assert names[0] == "message.created" and names[-1] == "done"
    assert "delta" in names and names.count("citation") == 1
    assert ev[0][1]["user_message"]["content"] == "What should I lead with?"
    done = ev[-1][1]["message"]
    assert done["status"] == "complete"
    assert "[E9]" not in done["content"] and "[E1]" in done["content"]  # unverifiable marker dropped
    cit = done["citations"][0]
    assert cit["source"] == "resume" and cit["chunk_id"] == 101
    assert cit["quote"] in "Built payment services in Python at Ledgerly."
    assert done["usage"] == {"tokens_in": 900, "tokens_out": 40}

    msgs = client.get(f"/api/v1/chat/threads/{tid}/messages", headers=h).json()
    assert [m["sender_role"] for m in msgs] == ["user", "assistant"]
    thread = client.get(f"/api/v1/chat/{app_id}/threads", headers=h).json()[0]
    assert thread["title"] == "What should I lead with?" and thread["message_count"] == 2
    db = TestingSessionLocal()
    try:
        e = db.query(UsageEvent).filter_by(user_id=user_id).one()
        assert (e.endpoint, e.tokens_in, e.tokens_out) == ("POST /chat/threads/{id}/messages", 900, 40)
        assert all(m.user_id == user_id for m in db.query(ApplicationChatMessage).all())
    finally:
        db.close()


def test_stream_budget_exceeded_is_a_plain_429(client, monkeypatch):
    h, user_id, app_id = _setup(client, "sse-budget@test.com")
    tid = _thread(client, h, app_id)
    db = TestingSessionLocal()
    try:
        db.get(User, user_id).token_budget_monthly = 5
        db.commit()
    finally:
        db.close()
    _stream_text(monkeypatch, ["never"])
    r = client.post(f"/api/v1/chat/threads/{tid}/messages", json={"content": "hi"}, headers=h)
    assert r.status_code == 429 and r.json()["detail"]["error"] == "token_budget_exceeded"
    assert client.get(f"/api/v1/chat/threads/{tid}/messages", headers=h).json() == []


def test_stop_keeps_partial_text(client, monkeypatch):
    h, _, app_id = _setup(client, "sse-stop@test.com")
    tid = _thread(client, h, app_id)

    def stop_now():
        db = TestingSessionLocal()
        try:
            m = db.query(ApplicationChatMessage).filter_by(sender_role="assistant", status="streaming").one()
            r = client.post(f"/api/v1/chat/messages/{m.id}/stop", headers=h)
            assert r.status_code == 200 and r.json()["status"] == "stopped"
        finally:
            db.close()

    _stream_text(monkeypatch, ["First part.", " Second part.", " Third part."], after_first=stop_now)
    ev = _events(client.post(f"/api/v1/chat/threads/{tid}/messages", json={"content": "tell me"}, headers=h).text)
    done = ev[-1][1]["message"]
    assert done["status"] == "stopped"
    assert done["content"].startswith("First part.") and "Third part." not in done["content"]


def test_provider_error_mid_stream(client, monkeypatch):
    h, _, app_id = _setup(client, "sse-err@test.com")
    tid = _thread(client, h, app_id)
    _stream_text(monkeypatch, ["Partial", "never"], fail_after=1)
    ev = _events(client.post(f"/api/v1/chat/threads/{tid}/messages", json={"content": "q"}, headers=h).text)
    assert ev[-1] == ("error", {"error": "provider_unavailable", "message": "The chat provider is temporarily unavailable."})
    msgs = client.get(f"/api/v1/chat/threads/{tid}/messages", headers=h).json()
    assert msgs[-1]["status"] == "error"


# ── intents ─────────────────────────────────────────────────────────────

def test_intent_routing():
    docs = {DocumentType.cover_letter: object()}
    assert coach.detect_intent("Shorten the cover letter", docs).kind == "edit"
    assert coach.detect_intent("Shorten the cold email", docs).note == "doc_missing"
    assert coach.detect_intent("Rewrite my resume summary", docs).note == "resume_edit"
    assert coach.detect_intent("Why is competition 46?", docs).kind == "score"
    assert coach.detect_intent("What questions will they ask?", docs).kind == "qa"


def test_score_answer_uses_scorer_numbers(client, monkeypatch):
    h, _, app_id = _setup(client, "sse-score@test.com")
    tid = _thread(client, h, app_id)
    seen = {}
    provider = get_llm_provider()

    def fake_stream(system_prompt, user_prompt):
        seen["prompt"] = user_prompt
        yield StreamChunk(text="It starts at 40 and moves with the posting's signals [E3].")

    # Patch the raw provider inside the usage meter so metering still runs.
    monkeypatch.setattr(getattr(provider, "inner", provider), "stream", fake_stream)
    ev = _events(client.post(f"/api/v1/chat/threads/{tid}/messages", json={"content": "Why is my competition score high?"}, headers=h).text)
    done = ev[-1][1]["message"]
    table = done["context"]["score"]
    expected = CompetitionScorer.breakdown(JD, "Northwind")
    assert table["competition_breakdown"] == expected
    assert abs(sum(r["delta"] for r in expected) - table["competition_score"]) < 0.05
    assert "do not recompute" in seen["prompt"]
    assert any(c["source"] == "score" for c in done["citations"])


def test_edit_proposal_reverts_invented_numbers(client, monkeypatch):
    h, _, app_id = _setup(client, "sse-edit@test.com")
    tid = _thread(client, h, app_id)
    _stream_text(monkeypatch, [
        "Dear team, I am applying for the role. ",
        "I built payment services in Python. I grew revenue by 300% in a year.",
    ])
    ev = _events(client.post(f"/api/v1/chat/threads/{tid}/messages", json={"content": "Shorten the cover letter"}, headers=h).text)
    names = [e for e, _ in ev]
    assert "proposed_edit" in names and "checking" in [d.get("phase") for e, d in ev if e == "status"]
    edit = dict(ev)["proposed_edit"]
    assert edit["doc_type"] == "cover_letter" and edit["base_version"] == 1
    assert "300%" in edit["reverted_numbers"]
    assert "300%" not in edit["new_content"]
    assert {"del", "keep"} <= {d["op"] for d in edit["diff"]}
    assert edit["word_count"]["after"] < edit["word_count"]["before"]


def _edit_message(client, monkeypatch, email):
    h, user_id, app_id = _setup(client, email)
    tid = _thread(client, h, app_id)
    _stream_text(monkeypatch, ["Dear team, I am applying for the role. I built payment services in Python."])
    ev = _events(client.post(f"/api/v1/chat/threads/{tid}/messages", json={"content": "Tighten the cover letter"}, headers=h).text)
    return h, user_id, app_id, ev[-1][1]["message"]["id"]


def test_apply_edit_creates_new_version_once(client, monkeypatch):
    h, _, app_id, mid = _edit_message(client, monkeypatch, "apply@test.com")
    r = client.post(f"/api/v1/chat/messages/{mid}/apply-edit", json={"base_version": 1}, headers=h)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["version"] == 2 and doc["doc_type"] == "cover_letter"
    current = client.get(f"/api/v1/applications/{app_id}/documents/current", headers=h).json()["cover_letter"]
    assert current["version"] == 2 and current["content"] == doc["content"]
    again = client.post(f"/api/v1/chat/messages/{mid}/apply-edit", json={"base_version": 1}, headers=h)
    assert again.status_code == 409 and again.json()["detail"]["error"] == "already_applied"


def test_apply_edit_stale_base(client, monkeypatch):
    h, user_id, app_id, mid = _edit_message(client, monkeypatch, "stale@test.com")
    db = TestingSessionLocal()
    try:
        db.add(GeneratedDocument(user_id=user_id, application_id=app_id, doc_type=DocumentType.cover_letter,
                                 version=2, content="newer", format="txt"))
        db.commit()
    finally:
        db.close()
    r = client.post(f"/api/v1/chat/messages/{mid}/apply-edit", json={"base_version": 1}, headers=h)
    assert r.status_code == 409 and r.json()["detail"] == {"error": "stale_base", "current_version": 2}


def test_apply_edit_rechecks_claims(client, monkeypatch):
    h, _, _, mid = _edit_message(client, monkeypatch, "recheck@test.com")
    monkeypatch.setattr(coach, "check_claims", lambda text, corpus: [{"claim": "Led 40 engineers", "confidence": 0.9}])
    r = client.post(f"/api/v1/chat/messages/{mid}/apply-edit", json={"base_version": 1}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["error"] == "unsupported_claims"


def test_apply_edit_ownership(client, monkeypatch):
    _, _, _, mid = _edit_message(client, monkeypatch, "own-a@test.com")
    other, _, _ = _setup(client, "own-b@test.com")
    assert client.post(f"/api/v1/chat/messages/{mid}/apply-edit", json={"base_version": 1}, headers=other).status_code == 404
    assert client.post(f"/api/v1/chat/messages/{mid}/stop", headers=other).status_code == 404


# ── legacy aliases + score response ─────────────────────────────────────

def test_legacy_endpoints_use_latest_thread(client, monkeypatch):
    h, _, app_id = _setup(client, "legacy@test.com")
    provider = get_llm_provider()
    monkeypatch.setattr(provider, "generate", lambda s, u: "Grounded answer [E1].")
    r = client.post(f"/api/v1/chat/{app_id}/messages", json={"content": "hello"}, headers=h)
    assert r.json() == {"ok": True}
    msgs = client.get(f"/api/v1/chat/{app_id}/messages", headers=h).json()
    assert [m["sender_role"] for m in msgs] == ["user", "assistant"]
    assert {"id", "sender_role", "content", "created_at"} <= set(msgs[0])  # old shape still present
    threads = client.get(f"/api/v1/chat/{app_id}/threads", headers=h).json()
    assert len(threads) == 1 and msgs[0]["thread_id"] == threads[0]["id"]


def test_score_response_has_competition_breakdown(client):
    h, _, app_id = _setup(client, "score-bd@test.com")
    db = TestingSessionLocal()
    try:
        u = db.query(User).filter_by(email="score-bd@test.com").one()
        db.add(UserFeature(user_id=u.id, feature=FeatureFlag.jd_analyze, enabled=True))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
    r = client.post(f"/api/v1/applications/{app_id}/score", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    rows = body["competition_breakdown"]
    assert rows[0] == {"signal": "base", "delta": 40.0, "evidence": None}
    assert abs(sum(x["delta"] for x in rows) - body["competition_score"]) < 0.05

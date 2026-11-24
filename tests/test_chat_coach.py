"""Coach chat, Phase 4a: budget enforcement, resume-scoped retrieval, JD in the prompt."""
import json

import pytest

from app.models.enums import FeatureFlag
from app.models.models import (
    ApplicationChatMessage,
    JobApplication,
    ParsedResumeData,
    UsageEvent,
    UsageLedger,
    User,
    UserFeature,
)
from app.services.ai.factory import get_llm_provider
from app.services.rag.service import RAGService
from tests.conftest import TestingSessionLocal, auth_headers

JD = "We need a Senior Backend Engineer with Python, PostgreSQL and Kubernetes. Immediate start."
ANALYSIS = {
    "keywords": ["microservices"],
    "required_skills": ["Python", "Kubernetes"],
    "preferred_skills": ["Kafka"],
    "strengths": ["Python"],
    "unsupported_gaps": ["Kubernetes in production"],
    "fit_summary": "Strong backend match.",
}


def _setup(client, email):
    h = auth_headers(client, email)
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter_by(email=email).first()
        db.add(UserFeature(user_id=user.id, feature=FeatureFlag.chat, enabled=True))
        resume = ParsedResumeData(user_id=user.id, raw_text="r", structured_json="{}", confidence_score=0.9)
        db.add(resume)
        db.flush()
        app = JobApplication(
            user_id=user.id,
            company_name="Northwind",
            role_title="Senior Backend Engineer",
            job_description=JD,
            jd_analysis_json=json.dumps(ANALYSIS),
            selected_resume_id=resume.id,
        )
        db.add(app)
        db.commit()
        return h, user.id, app.id, resume.id
    finally:
        db.close()


@pytest.fixture()
def rag_calls(monkeypatch):
    calls = []

    class _Chunk:
        def __init__(self, content):
            self.content = content

    def fake_search(self, query, top_k=5, parsed_resume_id=None):
        calls.append({"user_id": self.user_id, "parsed_resume_id": parsed_resume_id, "top_k": top_k})
        return [(_Chunk("Built payments services in Python at Ledgerly."), 0.9)]

    monkeypatch.setattr(RAGService, "search", fake_search)
    return calls


@pytest.fixture()
def prompts(monkeypatch):
    seen = []
    provider = get_llm_provider()

    def fake_generate(system_prompt, user_prompt):
        seen.append((system_prompt, user_prompt))
        return "Lead with the Ledgerly payments work [E1]. Kubernetes isn't in your resume."

    # Patch the raw provider inside the usage meter so metering still runs.
    monkeypatch.setattr(getattr(provider, "inner", provider), "generate", fake_generate)
    return seen


def test_retrieval_is_scoped_to_selected_resume(client, rag_calls, prompts):
    h, user_id, app_id, resume_id = _setup(client, "chat-scope@test.com")
    r = client.post(f"/api/v1/chat/{app_id}/messages", json={"content": "What should I lead with?"}, headers=h)
    assert r.status_code == 200, r.text
    assert rag_calls, "retrieval was not called"
    assert rag_calls[0]["parsed_resume_id"] == resume_id
    assert all(c["user_id"] == user_id for c in rag_calls)


def test_prompt_contains_jd_analysis_and_evidence(client, rag_calls, prompts):
    h, _, app_id, _ = _setup(client, "chat-prompt@test.com")
    client.post(f"/api/v1/chat/{app_id}/messages", json={"content": "Am I a fit?"}, headers=h)
    system, user = prompts[-1]
    assert "Never invent" in system
    assert JD in user
    assert "Kubernetes in production" in user  # unsupported gap carried into the prompt
    assert "[E1] (Resume · excerpt 1)\nBuilt payments services" in user
    assert "Question: Am I a fit?" in user


def test_budget_exceeded_returns_standard_error_and_stores_nothing(client, rag_calls, prompts):
    h, user_id, app_id, _ = _setup(client, "chat-budget@test.com")
    db = TestingSessionLocal()
    try:
        db.get(User, user_id).token_budget_monthly = 10
        db.commit()
    finally:
        db.close()

    r = client.post(f"/api/v1/chat/{app_id}/messages", json={"content": "Hello"}, headers=h)
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert detail["error"] == "token_budget_exceeded"
    assert detail["budget"] == 10
    assert set(detail) >= {"error", "used", "budget", "message"}
    assert prompts == []  # the LLM was never called

    db = TestingSessionLocal()
    try:
        assert db.query(ApplicationChatMessage).count() == 0
        assert db.query(UsageEvent).count() == 0
    finally:
        db.close()


def test_usage_is_recorded_against_the_chat_feature(client, rag_calls, prompts):
    h, user_id, app_id, _ = _setup(client, "chat-usage@test.com")
    r = client.post(f"/api/v1/chat/{app_id}/messages", json={"content": "Hello"}, headers=h)
    assert r.status_code == 200
    db = TestingSessionLocal()
    try:
        ev = db.query(UsageEvent).filter_by(user_id=user_id).one()
        assert ev.feature == FeatureFlag.chat
        assert ev.endpoint == "POST /chat/{id}/messages"
        assert ev.kind == "llm"
        assert ev.tokens_in > 0 and ev.tokens_out > 0
        ledger = db.query(UsageLedger).filter_by(user_id=user_id).one()
        assert ledger.tokens_used == ev.tokens_in + ev.tokens_out
    finally:
        db.close()
    msgs = client.get(f"/api/v1/chat/{app_id}/messages", headers=h).json()
    assert [m["sender_role"] for m in msgs] == ["user", "assistant"]


def test_cannot_chat_about_another_users_application(client, rag_calls, prompts):
    _, _, app_id, _ = _setup(client, "chat-owner@test.com")
    other, _, _, _ = _setup(client, "chat-other@test.com")
    assert client.post(f"/api/v1/chat/{app_id}/messages", json={"content": "hi"}, headers=other).status_code == 404
    assert client.get(f"/api/v1/chat/{app_id}/messages", headers=other).status_code == 404
    assert prompts == [] and rag_calls == []

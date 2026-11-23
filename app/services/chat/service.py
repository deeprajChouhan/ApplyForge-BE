from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import FeatureFlag
from app.models.models import ApplicationChat, ApplicationChatMessage, JobApplication
from app.services.ai.exceptions import AIProviderError
from app.services.ai.factory import get_llm_provider
from app.services.applications.service import ApplicationService
from app.services.usage.service import UsageTracker

# Budget pre-check reserves room for a reply of this many tokens. The provider
# doesn't cap output yet; streaming (Phase 4b) records the real count.
CHAT_REPLY_TOKEN_ALLOWANCE = 1500
JD_CHAR_LIMIT = 6000
HISTORY_TURNS = 6
EVIDENCE_TOP_K = 6

SYSTEM_PROMPT = (
    "You are Coach, the job-application assistant inside ApplyForge. "
    "Answer only from the material you are given: the candidate's resume evidence (numbered [E1], [E2], ...), "
    "the job description and its analysis. If that material doesn't support something, say so plainly. "
    "Never invent experience, employers, dates, numbers, skills or qualifications. "
    "When you rely on resume evidence, cite it by its number, e.g. [E2]. "
    "Gaps listed as having no resume evidence must stay gaps: suggest honest framing, never claim them. "
    "Be concise and practical."
)


def _analysis_block(app: JobApplication) -> str:
    if not app.jd_analysis_json:
        return "JD analysis: not run yet."
    try:
        a = json.loads(app.jd_analysis_json)
    except (TypeError, ValueError):
        return "JD analysis: unavailable."

    def line(label: str, key: str) -> str:
        vals = a.get(key) or []
        return f"- {label}: {', '.join(str(v) for v in vals) if vals else 'none'}"

    parts = [
        "JD analysis:",
        line("Required skills", "required_skills"),
        line("Preferred skills", "preferred_skills"),
        line("Keywords", "keywords"),
        line("Strengths backed by the resume", "strengths"),
        line("Gaps with no resume evidence", "unsupported_gaps"),
    ]
    if a.get("fit_summary"):
        parts.append(f"- Fit summary: {a['fit_summary']}")
    return "\n".join(parts)


class ChatService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id
        self.llm = get_llm_provider()
        self.apps = ApplicationService(db, user_id)

    def _application(self, application_id: int) -> JobApplication:
        # ApplicationService.get filters by user_id and raises 404 otherwise.
        return self.apps.get(application_id)

    def _get_chat(self, application_id: int) -> ApplicationChat:
        self._application(application_id)
        chat = self.db.query(ApplicationChat).filter_by(user_id=self.user_id, application_id=application_id).first()
        if not chat:
            chat = ApplicationChat(user_id=self.user_id, application_id=application_id)
            self.db.add(chat)
            self.db.commit()
            self.db.refresh(chat)
        return chat

    def _history_block(self, chat: ApplicationChat) -> str:
        rows = (
            self.db.query(ApplicationChatMessage)
            .filter_by(chat_id=chat.id)
            .order_by(ApplicationChatMessage.id.desc())
            .limit(HISTORY_TURNS)
            .all()
        )
        if not rows:
            return ""
        lines = [f"{'User' if r.sender_role == 'user' else 'Coach'}: {r.content}" for r in reversed(rows)]
        return "Conversation so far:\n" + "\n".join(lines)

    def build_prompt(self, app: JobApplication, chat: ApplicationChat, content: str) -> str:
        # Retrieval scoped to the resume pinned to this application, the same as /generate.
        evidence = self.apps._rag_evidence(app, f"{content}\n{app.role_title}", top_k=EVIDENCE_TOP_K)
        evidence_block = (
            "\n".join(f"[E{i}] {text}" for i, text in enumerate(evidence, start=1))
            if evidence
            else "No resume evidence found. Say that you can't see the resume rather than guessing."
        )
        jd = (app.job_description or "").strip()
        if len(jd) > JD_CHAR_LIMIT:
            jd = jd[:JD_CHAR_LIMIT] + " […]"
        sections = [
            f"Role: {app.role_title} at {app.company_name}",
            f"Job description:\n{jd or 'Not provided.'}",
            _analysis_block(app),
            f"Resume evidence:\n{evidence_block}",
        ]
        history = self._history_block(chat)
        if history:
            sections.append(history)
        sections.append(f"Question: {content}")
        return "\n\n".join(sections)

    def send(self, application_id: int, content: str):
        app = self._application(application_id)
        chat = self._get_chat(application_id)
        user_prompt = self.build_prompt(app, chat, content)

        tracker = UsageTracker(self.db, self.user_id, model=settings.llm_model)
        tokens_in = tracker.estimate_tokens(SYSTEM_PROMPT, user_prompt)
        # Same 429 {"error": "token_budget_exceeded", ...} shape as everywhere else.
        # Checked before anything is stored, so a refused message leaves no trace.
        tracker.check_budget(tokens_in + CHAT_REPLY_TOKEN_ALLOWANCE)

        try:
            reply = self.llm.generate(SYSTEM_PROMPT, user_prompt)
        except AIProviderError as exc:
            raise HTTPException(status_code=503, detail="Chat provider is temporarily unavailable") from exc

        self.db.add(ApplicationChatMessage(chat_id=chat.id, sender_role="user", content=content))
        self.db.add(ApplicationChatMessage(chat_id=chat.id, sender_role="assistant", content=reply))
        self.db.commit()
        tracker.record(FeatureFlag.chat, "chat.send", tokens_in, tracker.estimate_tokens(reply))

    def history(self, application_id: int):
        chat = self._get_chat(application_id)
        return self.db.query(ApplicationChatMessage).filter_by(chat_id=chat.id).order_by(ApplicationChatMessage.id.asc()).all()

from __future__ import annotations

import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import DocumentType, FeatureFlag
from app.models.models import (
    ApplicationChat,
    ApplicationChatMessage,
    GeneratedDocument,
    JobApplication,
    ParsedResumeData,
)
from app.services.ai.exceptions import AIProviderError
from app.services.ai.factory import get_llm_provider
from app.services.applications.service import ApplicationService
from app.services.chat import coach
from app.services.usage.service import UsageTracker

# Budget pre-check reserves room for a reply of this many tokens.
CHAT_REPLY_TOKEN_ALLOWANCE = 1500
HISTORY_TURNS = 6
TITLE_CHARS = 60

# Re-exported for tests and callers that relied on the 4a names.
SYSTEM_PROMPT = coach.SYSTEM_PROMPT


class ChatService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id
        self.llm = get_llm_provider()
        self.apps = ApplicationService(db, user_id)

    # ── ownership ──────────────────────────────────────────────────────
    def application(self, application_id: int) -> JobApplication:
        return self.apps.get(application_id)  # 404 unless owned and not deleted

    def thread(self, thread_id: int) -> ApplicationChat:
        t = self.db.query(ApplicationChat).filter_by(id=thread_id, user_id=self.user_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Thread not found")
        self.application(t.application_id)
        return t

    def message(self, message_id: int) -> ApplicationChatMessage:
        m = self.db.query(ApplicationChatMessage).filter_by(id=message_id, user_id=self.user_id).first()
        if not m:
            raise HTTPException(status_code=404, detail="Message not found")
        return m

    # ── threads ────────────────────────────────────────────────────────
    def list_threads(self, application_id: int) -> list[dict]:
        self.application(application_id)
        counts = dict(
            self.db.query(ApplicationChatMessage.chat_id, func.count(ApplicationChatMessage.id))
            .filter(ApplicationChatMessage.user_id == self.user_id)
            .group_by(ApplicationChatMessage.chat_id)
            .all()
        )
        rows = (
            self.db.query(ApplicationChat)
            .filter_by(user_id=self.user_id, application_id=application_id)
            .filter(ApplicationChat.archived_at.is_(None))
            .all()
        )
        rows.sort(key=lambda t: (t.last_message_at or t.created_at or datetime.min, t.id), reverse=True)
        return [self.thread_out(t, counts.get(t.id, 0)) for t in rows]

    def thread_out(self, t: ApplicationChat, count: int | None = None) -> dict:
        if count is None:
            count = self.db.query(func.count(ApplicationChatMessage.id)).filter_by(chat_id=t.id).scalar() or 0
        return {
            "id": t.id,
            "application_id": t.application_id,
            "title": t.title,
            "last_message_at": t.last_message_at,
            "message_count": count,
            "created_at": t.created_at,
        }

    def create_thread(self, application_id: int, title: str | None = None) -> ApplicationChat:
        self.application(application_id)
        t = ApplicationChat(user_id=self.user_id, application_id=application_id, title=(title or None))
        self.db.add(t)
        self.db.commit()
        self.db.refresh(t)
        return t

    def update_thread(self, thread_id: int, title: str | None, archived: bool | None) -> ApplicationChat:
        t = self.thread(thread_id)
        if title is not None:
            t.title = title.strip()[:120] or None
        if archived is not None:
            t.archived_at = datetime.utcnow() if archived else None
        self.db.commit()
        self.db.refresh(t)
        return t

    def latest_thread(self, application_id: int, create: bool) -> ApplicationChat | None:
        self.application(application_id)
        rows = (
            self.db.query(ApplicationChat)
            .filter_by(user_id=self.user_id, application_id=application_id)
            .filter(ApplicationChat.archived_at.is_(None))
            .all()
        )
        if rows:
            return max(rows, key=lambda t: (t.last_message_at or t.created_at or datetime.min, t.id))
        return self.create_thread(application_id) if create else None

    def messages(self, thread_id: int) -> list[ApplicationChatMessage]:
        t = self.thread(thread_id)
        return (
            self.db.query(ApplicationChatMessage)
            .filter_by(chat_id=t.id, user_id=self.user_id)
            .order_by(ApplicationChatMessage.id.asc())
            .all()
        )

    def history_block(self, thread: ApplicationChat, exclude_ids: tuple[int, ...] = ()) -> str:
        q = (
            self.db.query(ApplicationChatMessage)
            .filter_by(chat_id=thread.id)
            .filter(ApplicationChatMessage.status.in_(["complete", "stopped"]))
        )
        if exclude_ids:
            q = q.filter(~ApplicationChatMessage.id.in_(exclude_ids))
        rows = q.order_by(ApplicationChatMessage.id.desc()).limit(HISTORY_TURNS).all()
        if not rows:
            return ""
        lines = [f"{'User' if r.sender_role == 'user' else 'Coach'}: {r.content}" for r in reversed(rows)]
        return "Conversation so far:\n" + "\n".join(lines)

    # ── context (the "Knows" chips) ───────────────────────────────────
    def context(self, application_id: int) -> dict:
        app = self.application(application_id)
        from app.services.extension.service import ExtensionService
        from app.services.resume_tailoring.source import resolve_parsed_resume

        resume_row: ParsedResumeData | None = resolve_parsed_resume(self.db, self.user_id, app)
        docs = coach.current_documents(self.db, self.user_id, app.id)
        return {
            "resume": (
                {
                    "id": resume_row.id,
                    "label": ExtensionService(self.db, self.user_id).resume_label(resume_row) or f"Resume #{resume_row.id}",
                    "parse_confidence": resume_row.confidence_score,
                }
                if resume_row
                else None
            ),
            "jd": {"present": bool((app.job_description or "").strip()), "analysed": bool(app.jd_analysis_json)},
            "documents": [{"doc_type": dt.value, "version": d.version} for dt, d in docs.items()],
            "priority_score": app.priority_score,
        }

    # ── preparing a reply ─────────────────────────────────────────────
    def prepare(self, app: JobApplication, thread: ApplicationChat, content: str, exclude_ids=()) -> coach.Prepared:
        return coach.prepare(
            self.db,
            self.user_id,
            app,
            content,
            self.history_block(thread, exclude_ids),
            score_breakdown_fn=lambda: self.apps.compute_priority_score(app.id),
        )

    def finalize(self, prepared: coach.Prepared, app: JobApplication, raw: str) -> tuple[str, list[dict], dict | None]:
        """Turn the raw model output into (content, citations, proposed_edit)."""
        if prepared.intent.kind == "edit" and prepared.base_doc is not None:
            edit = coach.build_proposed_edit(
                raw, prepared.base_doc, coach.source_corpus(self.db, self.user_id, app), app.job_description or ""
            )
            return coach.edit_summary(edit), [], edit
        content, citations = coach.validate_citations(raw, prepared.evidence)
        if prepared.context.get("score"):
            # The table shown under a score answer is itself a source.
            if not any(c["source"] == "score" for c in citations):
                ev = next(e for e in prepared.evidence if e.source == "score")
                citations.append({
                    "id": f"c{len(citations) + 1}", "source": "score", "label": ev.label,
                    "quote": ev.text[: coach.QUOTE_CHARS], "chunk_id": None, "doc_type": None,
                    "doc_version": None, "char_start": 0, "char_end": len(ev.text[: coach.QUOTE_CHARS]),
                })
        return content, citations, None

    def touch(self, thread: ApplicationChat, first_user_text: str | None = None) -> None:
        thread.last_message_at = datetime.utcnow()
        if not thread.title and first_user_text:
            t = " ".join(first_user_text.split())
            thread.title = t if len(t) <= TITLE_CHARS else t[: TITLE_CHARS - 1].rstrip() + "…"

    # ── legacy, non-streaming (kept as aliases for the old drawer + extension) ──
    def send(self, application_id: int, content: str) -> ApplicationChatMessage:
        app = self.application(application_id)
        thread = self.latest_thread(application_id, create=True)
        prepared = self.prepare(app, thread, content)

        tracker = UsageTracker(self.db, self.user_id, model=settings.llm_model)
        tokens_in = tracker.estimate_tokens(prepared.system_prompt, prepared.user_prompt)
        tracker.check_budget(tokens_in + CHAT_REPLY_TOKEN_ALLOWANCE)

        try:
            raw = self.llm.generate(prepared.system_prompt, prepared.user_prompt)
        except AIProviderError as exc:
            raise HTTPException(status_code=503, detail="Chat provider is temporarily unavailable") from exc

        text, citations, edit = self.finalize(prepared, app, raw)
        tokens_out = tracker.estimate_tokens(raw)
        self.db.add(ApplicationChatMessage(chat_id=thread.id, user_id=self.user_id, sender_role="user", content=content))
        reply = ApplicationChatMessage(
            chat_id=thread.id, user_id=self.user_id, sender_role="assistant", content=text,
            citations_json=citations, proposed_edit_json=edit, context_json=prepared.context,
            tokens_in=tokens_in, tokens_out=tokens_out, model=settings.llm_model, status="complete",
        )
        self.db.add(reply)
        self.touch(thread, content)
        self.db.commit()
        # Spend is recorded by the metered provider; no tracker.record here.
        return reply

    def history(self, application_id: int) -> list[ApplicationChatMessage]:
        thread = self.latest_thread(application_id, create=False)
        return self.messages(thread.id) if thread else []

    # ── stop ───────────────────────────────────────────────────────────
    def stop(self, message_id: int) -> ApplicationChatMessage:
        m = self.message(message_id)
        if m.status == "streaming":
            m.status = "stopped"
            self.db.commit()
            self.db.refresh(m)
        return m

    # ── apply a proposed edit as a new document version ───────────────
    def apply_edit(self, message_id: int, base_version: int) -> GeneratedDocument:
        m = self.message(message_id)
        edit = m.proposed_edit_json
        if not edit or m.sender_role != "assistant":
            raise HTTPException(status_code=404, detail="No proposed edit on this message")
        thread = self.thread(m.chat_id)
        app = self.application(thread.application_id)

        if m.applied_document_id:
            raise HTTPException(status_code=409, detail={"error": "already_applied", "document_id": m.applied_document_id})

        doc_type = DocumentType(edit["doc_type"])
        current = coach.current_documents(self.db, self.user_id, app.id).get(doc_type)
        current_version = current.version if current else 0
        if base_version != edit["base_version"] or current_version != edit["base_version"]:
            raise HTTPException(status_code=409, detail={"error": "stale_base", "current_version": current_version})

        # Defence in depth: re-check at apply time, against today's resume.
        flags = coach.check_claims(edit["new_content"], coach.source_corpus(self.db, self.user_id, app))
        if flags:
            raise HTTPException(status_code=422, detail={"error": "unsupported_claims", "flags": flags})

        row = GeneratedDocument(
            user_id=self.user_id,
            application_id=app.id,
            doc_type=doc_type,
            version=current_version + 1,
            content=edit["new_content"],
            format=current.format if current else "txt",
            resume_template_id=current.resume_template_id if current else None,
        )
        self.db.add(row)
        self.db.flush()
        m.applied_document_id = row.id
        self.db.commit()
        self.db.refresh(row)
        return row


def message_payload(m: ApplicationChatMessage) -> dict:
    from app.schemas.chat import ChatMessageOut

    return json.loads(ChatMessageOut.from_row(m).model_dump_json())

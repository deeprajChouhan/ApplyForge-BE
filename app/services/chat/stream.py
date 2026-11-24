"""
Server-sent events for POST /chat/threads/{thread_id}/messages.

Events (each `event: <name>` + `data: <json>`):
  message.created  {"user_message": ChatMessageOut, "assistant_message_id": int}
  status           {"phase": "retrieving" | "generating" | "checking"}
  delta            {"text": str}
  citation         Citation
  proposed_edit    ProposedEdit
  done             {"message": ChatMessageOut}        (status complete or stopped)
  error            {"error": "provider_unavailable" | "internal", "message": str}

The budget is checked before the stream opens: an over-budget request gets the
standard HTTP 429 token_budget_exceeded body, never a half-open stream.

The generator runs after the request's DB session has been released, so it
opens its own session on the same engine. It never retries the provider: a
retry would duplicate text and double-charge tokens.
"""
from __future__ import annotations

import json
import time
from typing import Iterator

import structlog
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.enums import FeatureFlag
from app.models.models import ApplicationChat, ApplicationChatMessage, GeneratedDocument, JobApplication
from app.services.ai.exceptions import AIProviderError
from app.services.ai.factory import get_llm_provider
from app.services.chat.coach import Prepared
from app.services.chat.service import ChatService, message_payload
from app.services.usage.service import UsageTracker

logger = structlog.get_logger(__name__)
STOP_POLL_SECONDS = 0.3


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def stream_reply(
    bind,
    user_id: int,
    thread_id: int,
    application_id: int,
    user_message_id: int,
    assistant_message_id: int,
    prepared: Prepared,
    tokens_in_estimate: int,
    base_document_id: int | None = None,
) -> Iterator[str]:
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=bind)
    db: Session = SessionLocal()
    llm = get_llm_provider()
    svc = ChatService(db, user_id)
    parts: list[str] = []
    usage: dict | None = None
    stopped = False
    finished = False

    def is_stopped() -> bool:
        check = SessionLocal()
        try:
            status = (
                check.query(ApplicationChatMessage.status)
                .filter_by(id=assistant_message_id, user_id=user_id)
                .scalar()
            )
            return status == "stopped"
        finally:
            check.close()

    def save(status: str) -> ApplicationChatMessage:
        app = db.query(JobApplication).filter_by(id=application_id, user_id=user_id).one()
        thread = db.query(ApplicationChat).filter_by(id=thread_id, user_id=user_id).one()
        msg = db.query(ApplicationChatMessage).filter_by(id=assistant_message_id, user_id=user_id).one()
        raw = "".join(parts)
        if status == "error":
            content, citations, edit = raw, [], None
        else:
            content, citations, edit = svc.finalize(prepared, app, raw) if raw else ("", [], None)
        tracker = UsageTracker(db, user_id, model=settings.llm_model)
        t_in = (usage or {}).get("in") or tokens_in_estimate
        t_out = (usage or {}).get("out") or tracker.estimate_tokens(raw)
        msg.content = content
        msg.status = status
        msg.citations_json = citations
        msg.proposed_edit_json = edit
        msg.context_json = prepared.context
        msg.tokens_in, msg.tokens_out, msg.model = t_in, t_out, settings.llm_model
        svc.touch(thread)
        db.commit()
        db.refresh(msg)
        # Spend is recorded by the metered provider (even on early stop).
        return msg

    if base_document_id is not None:
        # Re-attach in this session: the request session committed and expired its copy.
        prepared.base_doc = db.query(GeneratedDocument).filter_by(id=base_document_id, user_id=user_id).one_or_none()

    try:
        user_msg = db.query(ApplicationChatMessage).filter_by(id=user_message_id, user_id=user_id).one()
        yield sse("message.created", {"user_message": message_payload(user_msg), "assistant_message_id": assistant_message_id})
        yield sse("status", {"phase": "generating"})

        editing = prepared.intent.kind == "edit"
        last_poll = time.monotonic()
        try:
            for chunk in llm.stream(prepared.system_prompt, prepared.user_prompt):
                if chunk.usage:
                    usage = chunk.usage
                if chunk.text:
                    parts.append(chunk.text)
                    if not editing:
                        yield sse("delta", {"text": chunk.text})
                if time.monotonic() - last_poll >= STOP_POLL_SECONDS:
                    last_poll = time.monotonic()
                    if is_stopped():
                        stopped = True
                        break
            if not stopped and is_stopped():
                stopped = True
        except AIProviderError as exc:
            logger.warning("chat_stream_provider_error", error=str(exc)[:200])
            save("error")
            finished = True
            yield sse("error", {"error": "provider_unavailable", "message": "The chat provider is temporarily unavailable."})
            return

        if editing and not stopped:
            yield sse("status", {"phase": "checking"})
        msg = save("stopped" if stopped else "complete")
        finished = True
        out = message_payload(msg)
        if editing and not stopped:
            yield sse("delta", {"text": out["content"]})
        for c in out["citations"]:
            yield sse("citation", c)
        if out["proposed_edit"]:
            yield sse("proposed_edit", out["proposed_edit"])
        yield sse("done", {"message": out})
    except GeneratorExit:
        # Client went away: keep what was generated, mark it stopped, bill it.
        if not finished:
            try:
                save("stopped")
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("chat_stream_disconnect_save_failed", error=str(exc)[:200])
        raise
    except Exception as exc:
        logger.error("chat_stream_failed", error=str(exc)[:300])
        if not finished:
            try:
                db.rollback()
                save("error")
            except Exception:
                pass
        yield sse("error", {"error": "internal", "message": "Something went wrong while answering."})
    finally:
        db.close()

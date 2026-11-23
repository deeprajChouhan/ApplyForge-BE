from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_feature
from app.core.config import settings
from app.db.session import get_db
from app.models.enums import FeatureFlag
from app.models.models import ApplicationChatMessage, User
from app.schemas.application import GeneratedDocumentOut
from app.schemas.chat import (
    ApplyEditRequest,
    ChatContextOut,
    ChatMessageCreate,
    ChatMessageOut,
    ChatThreadCreate,
    ChatThreadOut,
    ChatThreadUpdate,
)
from app.services.chat.service import CHAT_REPLY_TOKEN_ALLOWANCE, ChatService
from app.services.chat.stream import stream_reply
from app.services.usage.service import UsageTracker

router = APIRouter(prefix="/chat", tags=["chat"])

_need_chat = Depends(require_feature(FeatureFlag.chat))


# ── Context + threads ────────────────────────────────────────────────────

@router.get("/{application_id}/context", response_model=ChatContextOut, dependencies=[_need_chat])
def context(application_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ChatService(db, user.id).context(application_id)


@router.get("/{application_id}/threads", response_model=list[ChatThreadOut], dependencies=[_need_chat])
def list_threads(application_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ChatService(db, user.id).list_threads(application_id)


@router.post("/{application_id}/threads", response_model=ChatThreadOut, status_code=201, dependencies=[_need_chat])
def create_thread(
    application_id: int,
    payload: ChatThreadCreate | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    svc = ChatService(db, user.id)
    t = svc.create_thread(application_id, payload.title if payload else None)
    return svc.thread_out(t, 0)


@router.patch("/threads/{thread_id}", response_model=ChatThreadOut, dependencies=[_need_chat])
def update_thread(
    thread_id: int, payload: ChatThreadUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    svc = ChatService(db, user.id)
    return svc.thread_out(svc.update_thread(thread_id, payload.title, payload.archived))


@router.get("/threads/{thread_id}/messages", response_model=list[ChatMessageOut], dependencies=[_need_chat])
def thread_messages(thread_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [ChatMessageOut.from_row(m) for m in ChatService(db, user.id).messages(thread_id)]


# ── Streaming send ───────────────────────────────────────────────────────

@router.post("/threads/{thread_id}/messages", dependencies=[_need_chat])
def send_streaming(
    thread_id: int, payload: ChatMessageCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    svc = ChatService(db, user.id)
    thread = svc.thread(thread_id)
    app = svc.application(thread.application_id)
    prepared = svc.prepare(app, thread, payload.content)

    tracker = UsageTracker(db, user.id, model=settings.llm_model)
    tokens_in = tracker.estimate_tokens(prepared.system_prompt, prepared.user_prompt)
    # Standard 429 token_budget_exceeded body, before anything is stored or streamed.
    tracker.check_budget(tokens_in + CHAT_REPLY_TOKEN_ALLOWANCE)

    base_document_id = prepared.base_doc.id if prepared.base_doc is not None else None
    user_msg = ApplicationChatMessage(chat_id=thread.id, user_id=user.id, sender_role="user", content=payload.content)
    assistant = ApplicationChatMessage(
        chat_id=thread.id, user_id=user.id, sender_role="assistant", content="", status="streaming",
        context_json=prepared.context, model=settings.llm_model,
    )
    db.add_all([user_msg, assistant])
    svc.touch(thread, payload.content)
    db.commit()

    return StreamingResponse(
        stream_reply(
            db.get_bind(), user.id, thread.id, app.id, user_msg.id, assistant.id, prepared, tokens_in, base_document_id
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/messages/{message_id}/stop", response_model=ChatMessageOut, dependencies=[_need_chat])
def stop(message_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ChatMessageOut.from_row(ChatService(db, user.id).stop(message_id))


@router.post("/messages/{message_id}/apply-edit", response_model=GeneratedDocumentOut, dependencies=[_need_chat])
def apply_edit(
    message_id: int, payload: ApplyEditRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return ChatService(db, user.id).apply_edit(message_id, payload.base_version)


# ── Legacy (deprecated): most recent thread of the application ──────────

@router.post("/{application_id}/messages", dependencies=[_need_chat], deprecated=True)
def send_message(
    application_id: int, payload: ChatMessageCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    ChatService(db, user.id).send(application_id, payload.content)
    return {"ok": True}


@router.get("/{application_id}/messages", response_model=list[ChatMessageOut], dependencies=[_need_chat], deprecated=True)
def history(application_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [ChatMessageOut.from_row(m) for m in ChatService(db, user.id).history(application_id)]

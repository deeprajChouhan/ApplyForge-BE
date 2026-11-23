from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import DocumentType


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class Citation(BaseModel):
    id: str
    source: Literal["resume", "jd", "document", "score"]
    label: str
    quote: str
    chunk_id: int | None = None
    doc_type: DocumentType | None = None
    doc_version: int | None = None
    char_start: int = 0
    char_end: int = 0


class DiffOp(BaseModel):
    op: Literal["keep", "add", "del"]
    text: str


class ProposedEdit(BaseModel):
    doc_type: DocumentType
    base_document_id: int
    base_version: int
    new_content: str
    diff: list[DiffOp]
    word_count: dict[str, int]
    unsupported_claims: list[dict] = []
    reverted_numbers: list[str] = []


class ChatUsage(BaseModel):
    tokens_in: int | None = None
    tokens_out: int | None = None


class ChatMessageOut(BaseModel):
    id: int
    thread_id: int
    sender_role: str
    status: str
    content: str
    citations: list[Citation] = []
    proposed_edit: ProposedEdit | None = None
    applied_document_id: int | None = None
    context: dict | None = None
    usage: ChatUsage | None = None
    created_at: datetime

    @classmethod
    def from_row(cls, m) -> "ChatMessageOut":
        return cls(
            id=m.id,
            thread_id=m.chat_id,
            sender_role=m.sender_role,
            status=m.status or "complete",
            content=m.content or "",
            citations=m.citations_json or [],
            proposed_edit=m.proposed_edit_json,
            applied_document_id=m.applied_document_id,
            context=m.context_json,
            usage=ChatUsage(tokens_in=m.tokens_in, tokens_out=m.tokens_out) if (m.tokens_in or m.tokens_out) else None,
            created_at=m.created_at,
        )


class ChatThreadCreate(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class ChatThreadUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    archived: bool | None = None


class ChatThreadOut(BaseModel):
    id: int
    application_id: int
    title: str | None
    last_message_at: datetime | None
    message_count: int
    created_at: datetime


class ContextResume(BaseModel):
    id: int
    label: str
    parse_confidence: float | None


class ContextDocument(BaseModel):
    doc_type: DocumentType
    version: int


class ChatContextOut(BaseModel):
    resume: ContextResume | None
    jd: dict[str, bool]
    documents: list[ContextDocument]
    priority_score: float | None


class ApplyEditRequest(BaseModel):
    base_version: int = Field(ge=1)

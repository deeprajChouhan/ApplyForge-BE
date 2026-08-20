"""
Answer Library service.

Handles storing, looking up, and seeding a per-user library of application-form
answers, with optional semantic (embedding-based) lookup on top of exact
normalized-question matching.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.answer import AnswerLibrary, FieldType
from app.schemas.answer import AnswerLookupOut, AnswerSeedResult

# TODO: RAGService (app.services.rag.service.RAGService) may already provide a
# reusable embedding helper -- if so, prefer wiring this service to that
# instead of calling OpenAI directly here. Its exact signature wasn't
# available at the time this was written, so we call OpenAI directly and
# degrade gracefully (embedding=None) on any failure.
try:
    from app.core.config import settings
except Exception:  # pragma: no cover - defensive import
    settings = None  # type: ignore[assignment]

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
_MAX_EMBED_CHARS = 8000
_SEMANTIC_MATCH_THRESHOLD = 0.82
_LOOKUP_CANDIDATE_LIMIT = 200

_STOPWORD_PREFIXES = ("the ", "a ", "an ")


# ---------------------------------------------------------------------------
# Seed data: 30 standard ATS questions.
# question_text -> (field_type, tag)
# ---------------------------------------------------------------------------
_SEED_QUESTIONS: list[tuple[str, str, str]] = [
    ("Are you legally authorized to work in this country?", "boolean", "work_auth"),
    (
        "Will you now or in the future require sponsorship for employment visa status?",
        "boolean",
        "sponsorship",
    ),
    ("How did you hear about this position?", "short_text", "referral"),
    ("What are your salary expectations?", "short_text", "salary"),
    ("What is your desired salary in USD?", "short_text", "salary"),
    ("When can you start?", "short_text", "availability"),
    ("What is your notice period?", "short_text", "availability"),
    ("Are you open to relocation?", "boolean", "relocation"),
    ("Are you open to remote / hybrid / on-site work?", "single_select", "work_mode"),
    ("Do you have a preferred location?", "short_text", "location"),
    ("Why are you interested in this role?", "long_text", "motivation"),
    ("Why are you interested in working at our company?", "long_text", "motivation"),
    ("Tell us about yourself.", "long_text", "intro"),
    ("What is your LinkedIn profile URL?", "url", "links"),
    ("What is your GitHub profile URL?", "url", "links"),
    ("What is your portfolio URL?", "url", "links"),
    ("Please provide a link to your personal website.", "url", "links"),
    ("What are your pronouns?", "short_text", "pronouns"),
    ("Do you have any pending offers?", "short_text", "offers"),
    (
        "What is your years of experience with [primary skill]?",
        "short_text",
        "experience",
    ),
    (
        "How many years of professional experience do you have?",
        "short_text",
        "experience",
    ),
    ("Are you willing to travel for this role?", "boolean", "travel"),
    ("What is your gender?", "single_select", "eeoc"),
    ("What is your race or ethnicity?", "single_select", "eeoc"),
    ("Are you a veteran?", "single_select", "eeoc"),
    ("Do you have a disability?", "single_select", "eeoc"),
    ("What is your date of birth?", "date", "personal"),
    ("Please describe your work authorization status.", "long_text", "work_auth"),
    ("What is your current employer?", "short_text", "current_employer"),
    ("What is your current job title?", "short_text", "current_title"),
]

_EEOC_TAG = "eeoc"


def _to_field_type(ft: Any) -> FieldType:
    if isinstance(ft, FieldType):
        return ft
    if not ft:
        return FieldType.SHORT_TEXT
    val = str(ft).lower().strip()
    for member in FieldType:
        if member.value == val or member.name.lower() == val:
            return member
    return FieldType.SHORT_TEXT


class AnswerLibraryService:
    """Per-user CRUD + lookup + seeding for the Answer Library."""

    def __init__(self, db: Session, user_id: int) -> None:
        self.db = db
        self.user_id = user_id

    # ------------------------------------------------------------------
    # Normalization / hashing helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize(text: str) -> str:
        normalized = (text or "").strip().lower()
        normalized = re.sub(r"\s+", " ", normalized)
        # strip punctuation except '?'
        normalized = re.sub(r"[^\w\s?]", "", normalized)
        normalized = normalized.strip()
        for prefix in _STOPWORD_PREFIXES:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :]
                break
        return normalized.strip()

    @staticmethod
    def _question_key(text: str) -> str:
        normalized = AnswerLibraryService._normalize(text)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------
    def _embed(self, text: str) -> Optional[list[float]]:
        """Best-effort embedding lookup. Returns None on any failure so
        semantic lookup simply degrades to exact-match-only."""
        try:
            from openai import OpenAI

            api_key = getattr(settings, "openai_api_key", None) if settings else None
            api_key = api_key or os.getenv("OPENAI_API_KEY")
            if not api_key:
                return None

            client = OpenAI(api_key=api_key)
            truncated = (text or "")[:_MAX_EMBED_CHARS]
            if not truncated.strip():
                return None
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=truncated,
            )
            return list(response.data[0].embedding)
        except Exception:
            return None

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for x, y in zip(a, b):
            dot += x * y
            norm_a += x * x
            norm_b += y * y
        if norm_a <= 0.0 or norm_b <= 0.0:
            return 0.0
        return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def upsert(
        self,
        question_text: str,
        answer_text: str,
        field_type: str,
        tags: Optional[str] = None,
        confidence: Optional[float] = None,
        source: str = "user",
    ) -> AnswerLibrary:
        question_key = self._question_key(question_text)
        enum_field_type = _to_field_type(field_type)

        existing = self.db.execute(
            select(AnswerLibrary).where(
                AnswerLibrary.user_id == self.user_id,
                AnswerLibrary.question_key == question_key,
            )
        ).scalar_one_or_none()

        now = datetime.utcnow()

        if existing is not None:
            question_changed = existing.question_text != question_text
            answer_changed = existing.answer_text != answer_text
            existing.question_text = question_text
            existing.answer_text = answer_text
            existing.field_type = enum_field_type
            if tags is not None:
                existing.tags = tags
            if confidence is not None:
                existing.confidence = confidence
            existing.source = source
            existing.updated_at = now
            if question_changed or answer_changed:
                existing.question_embedding_json = self._embed(question_text)
            self.db.add(existing)
            self.db.commit()
            self.db.refresh(existing)
            return existing

        embedding = self._embed(question_text)
        record = AnswerLibrary(
            user_id=self.user_id,
            question_key=question_key,
            question_text=question_text,
            question_embedding_json=embedding,
            answer_text=answer_text,
            field_type=enum_field_type,
            tags=tags,
            confidence=confidence,
            source=source,
            times_used=0,
            last_used_at=None,
            created_at=now,
            updated_at=now,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def list(
        self,
        field_type: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[AnswerLibrary]:
        stmt = select(AnswerLibrary).where(AnswerLibrary.user_id == self.user_id)
        if field_type:
            stmt = stmt.where(AnswerLibrary.field_type == field_type)
        if tag:
            stmt = stmt.where(AnswerLibrary.tags.like(f"%{tag}%"))
        stmt = stmt.order_by(AnswerLibrary.updated_at.desc()).limit(limit).offset(offset)
        return list(self.db.execute(stmt).scalars().all())

    def get(self, answer_id: int) -> Optional[AnswerLibrary]:
        return self.db.execute(
            select(AnswerLibrary).where(
                AnswerLibrary.id == answer_id,
                AnswerLibrary.user_id == self.user_id,
            )
        ).scalar_one_or_none()

    def update(self, answer_id: int, patch: dict[str, Any]) -> Optional[AnswerLibrary]:
        record = self.get(answer_id)
        if record is None:
            return None

        new_question_text = patch.get("question_text")
        question_changed = (
            new_question_text is not None and new_question_text != record.question_text
        )

        for field in (
            "question_text",
            "answer_text",
            "field_type",
            "tags",
            "confidence",
            "source",
        ):
            if field in patch and patch[field] is not None:
                setattr(record, field, patch[field])

        if question_changed:
            record.question_key = self._question_key(record.question_text)
            record.question_embedding_json = self._embed(record.question_text)

        record.updated_at = datetime.utcnow()
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def delete(self, answer_id: int) -> bool:
        record = self.get(answer_id)
        if record is None:
            return False
        self.db.delete(record)
        self.db.commit()
        return True

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def lookup(
        self, question_text: str, field_type: Optional[str] = None
    ) -> AnswerLookupOut:
        question_key = self._question_key(question_text)

        exact_stmt = select(AnswerLibrary).where(
            AnswerLibrary.user_id == self.user_id,
            AnswerLibrary.question_key == question_key,
        )
        if field_type:
            exact_stmt = exact_stmt.where(AnswerLibrary.field_type == field_type)
        exact_match = self.db.execute(exact_stmt).scalar_one_or_none()

        if exact_match is not None:
            self._mark_used(exact_match)
            return AnswerLookupOut(
                matched=True,
                match_type="exact",
                confidence=1.0,
                answer=exact_match,  # type: ignore[arg-type]
            )

        query_embedding = self._embed(question_text)
        if query_embedding is not None:
            candidates_stmt = select(AnswerLibrary).where(
                AnswerLibrary.user_id == self.user_id,
                AnswerLibrary.question_embedding_json.is_not(None),
            )
            if field_type:
                candidates_stmt = candidates_stmt.where(
                    AnswerLibrary.field_type == field_type
                )
            candidates_stmt = candidates_stmt.limit(_LOOKUP_CANDIDATE_LIMIT)
            candidates = self.db.execute(candidates_stmt).scalars().all()

            best_candidate: Optional[AnswerLibrary] = None
            best_score = 0.0
            for candidate in candidates:
                embedding = candidate.question_embedding_json
                if not embedding:
                    continue
                score = self._cosine_similarity(query_embedding, embedding)
                if score > best_score:
                    best_score = score
                    best_candidate = candidate

            if best_candidate is not None and best_score >= _SEMANTIC_MATCH_THRESHOLD:
                self._mark_used(best_candidate)
                return AnswerLookupOut(
                    matched=True,
                    match_type="semantic",
                    confidence=best_score,
                    answer=best_candidate,  # type: ignore[arg-type]
                )

        return AnswerLookupOut(
            matched=False,
            match_type="none",
            confidence=None,
            answer=None,
        )

    def _mark_used(self, record: AnswerLibrary) -> None:
        record.times_used = (record.times_used or 0) + 1
        record.last_used_at = datetime.utcnow()
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------
    def seed_common_questions(self) -> AnswerSeedResult:
        created = 0
        skipped = 0
        now = datetime.utcnow()

        for question_text, field_type, tag in _SEED_QUESTIONS:
            question_key = self._question_key(question_text)
            existing = self.db.execute(
                select(AnswerLibrary).where(
                    AnswerLibrary.user_id == self.user_id,
                    AnswerLibrary.question_key == question_key,
                )
            ).scalar_one_or_none()
            if existing is not None:
                skipped += 1
                continue

            is_eeoc = tag == _EEOC_TAG
            record = AnswerLibrary(
                user_id=self.user_id,
                question_key=question_key,
                question_text=question_text,
                question_embedding_json=None,
                answer_text="Prefer not to say" if is_eeoc else "",
                field_type=_to_field_type(field_type),
                tags=tag,
                confidence=1.0 if is_eeoc else None,
                source="seed",
                times_used=0,
                last_used_at=None,
                created_at=now,
                updated_at=now,
            )
            self.db.add(record)
            created += 1

        self.db.commit()
        return AnswerSeedResult(
            created=created,
            skipped=skipped,
            total=len(_SEED_QUESTIONS),
        )

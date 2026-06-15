"""
InterviewService — AI mock interview practice.

Starting a session generates a small set of realistic interview questions
(tailored to the role title, and the linked application's job description
when provided) via the configured LLM provider, with a static fallback set
if generation fails. Submitting an answer generates short, constructive AI
feedback for that answer; once every question in a session has been
answered, the session is marked `completed`.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.models.models import InterviewAnswer, InterviewSession, JobApplication
from app.services.ai.factory import get_llm_provider
from app.services.analytics.service import ProductEventService
from app.services.audit.service import AuditService

logger = logging.getLogger(__name__)

QUESTIONS_PER_SESSION = 5

_FALLBACK_QUESTIONS = [
    "Tell me about yourself and why you're interested in this role.",
    "Describe a challenging project you worked on. What was your role and how did you handle obstacles?",
    "How do you prioritize tasks when working on multiple projects with tight deadlines?",
    "Tell me about a time you disagreed with a teammate or manager. How did you resolve it?",
    "Where do you see yourself growing in this role over the next couple of years?",
]


def _strip_code_fences(text: str) -> str:
    if text.startswith("```"):
        parts = text.split("```")
        inner = parts[1]
        if inner.startswith("json"):
            inner = inner[4:]
        return inner.strip()
    return text


class InterviewService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id
        self.llm = get_llm_provider()

    # ── Read ──────────────────────────────────────────────────────────────

    def list_sessions(self) -> list[InterviewSessionListItemData]:
        sessions = (
            self.db.query(InterviewSession)
            .filter_by(user_id=self.user_id)
            .filter(InterviewSession.deleted_at.is_(None))
            .order_by(InterviewSession.created_at.desc())
            .all()
        )

        results: list[InterviewSessionListItemData] = []
        for session in sessions:
            answers = (
                self.db.query(InterviewAnswer)
                .filter_by(session_id=session.id)
                .filter(InterviewAnswer.deleted_at.is_(None))
                .all()
            )
            results.append(InterviewSessionListItemData(
                session=session,
                question_count=len(answers),
                answered_count=sum(1 for a in answers if a.answer is not None),
            ))
        return results

    def get_session(self, session_id: int) -> InterviewSession:
        session = (
            self.db.query(InterviewSession)
            .filter_by(id=session_id, user_id=self.user_id)
            .filter(InterviewSession.deleted_at.is_(None))
            .first()
        )
        if not session:
            raise HTTPException(status_code=404, detail="Interview session not found")
        return session

    def get_session_with_answers(self, session_id: int) -> tuple[InterviewSession, list[InterviewAnswer]]:
        session = self.get_session(session_id)
        answers = (
            self.db.query(InterviewAnswer)
            .filter_by(session_id=session.id)
            .filter(InterviewAnswer.deleted_at.is_(None))
            .order_by(InterviewAnswer.question_index)
            .all()
        )
        return session, answers

    # ── Write ─────────────────────────────────────────────────────────────

    def start_session(
        self,
        role_title: str,
        application_id: Optional[int] = None,
        request: Request | None = None,
    ) -> InterviewSession:
        jd_text: str | None = None
        if application_id is not None:
            app = (
                self.db.query(JobApplication)
                .filter_by(id=application_id, user_id=self.user_id)
                .filter(JobApplication.deleted_at.is_(None))
                .first()
            )
            if not app:
                raise HTTPException(status_code=404, detail="Application not found")
            jd_text = app.job_description

        questions = self._generate_questions(role_title, jd_text)

        session = InterviewSession(
            user_id=self.user_id,
            application_id=application_id,
            role_title=role_title,
            status="in_progress",
        )
        self.db.add(session)
        self.db.flush()

        for index, question in enumerate(questions):
            self.db.add(InterviewAnswer(session_id=session.id, question_index=index, question=question))

        self.db.commit()
        self.db.refresh(session)

        AuditService(self.db).log(
            action="interview_started",
            entity_type="interview_session",
            entity_id=session.id,
            actor_user_id=self.user_id,
            actor_role="user",
            after={"role_title": role_title, "application_id": application_id},
            request=request,
        )
        ProductEventService(self.db).track(
            "interview_started",
            user_id=self.user_id,
            entity_type="interview_session",
            entity_id=session.id,
            properties={"role_title": role_title, "question_count": len(questions)},
            request=request,
        )

        return session

    def submit_answer(
        self,
        session_id: int,
        question_index: int,
        answer: str,
        request: Request | None = None,
    ) -> InterviewAnswer:
        session = self.get_session(session_id)

        qa = (
            self.db.query(InterviewAnswer)
            .filter_by(session_id=session.id, question_index=question_index)
            .filter(InterviewAnswer.deleted_at.is_(None))
            .first()
        )
        if not qa:
            raise HTTPException(status_code=404, detail="Question not found")

        qa.answer = answer
        qa.feedback = self._generate_feedback(session.role_title, qa.question, answer)
        self.db.commit()
        self.db.refresh(qa)

        AuditService(self.db).log(
            action="interview_answer_submitted",
            entity_type="interview_answer",
            entity_id=qa.id,
            actor_user_id=self.user_id,
            actor_role="user",
            after={"question_index": question_index},
            request=request,
        )
        ProductEventService(self.db).track(
            "interview_answer_submitted",
            user_id=self.user_id,
            entity_type="interview_session",
            entity_id=session.id,
            properties={"question_index": question_index},
            request=request,
        )

        all_answers = (
            self.db.query(InterviewAnswer)
            .filter_by(session_id=session.id)
            .filter(InterviewAnswer.deleted_at.is_(None))
            .all()
        )
        if all_answers and all(a.answer is not None for a in all_answers) and session.status != "completed":
            session.status = "completed"
            self.db.commit()
            ProductEventService(self.db).track(
                "interview_completed",
                user_id=self.user_id,
                entity_type="interview_session",
                entity_id=session.id,
                properties={"role_title": session.role_title, "question_count": len(all_answers)},
                request=request,
            )

        return qa

    # ── AI generation helpers ────────────────────────────────────────────

    def _generate_questions(self, role_title: str, jd_text: str | None) -> list[str]:
        system_prompt = (
            "You are an experienced technical interviewer. Generate realistic mock interview "
            f"questions for a candidate interviewing for the role of '{role_title}'. "
            "Mix behavioral and role-specific questions. "
            "Return ONLY a valid JSON object with no markdown fences, using this exact schema:\n"
            '{"questions": ["question 1", "question 2", ...]}'
        )
        user_prompt = f"Role: {role_title}\n"
        if jd_text:
            user_prompt += f"\nJob description for context:\n{jd_text[:4000]}"
        user_prompt += f"\n\nGenerate exactly {QUESTIONS_PER_SESSION} interview questions."

        try:
            response_text = _strip_code_fences(self.llm.generate(system_prompt, user_prompt).strip())
            result = json.loads(response_text)
            questions = [str(q).strip() for q in result.get("questions", []) if str(q).strip()]
            if questions:
                return questions[:QUESTIONS_PER_SESSION]
        except Exception as exc:
            logger.warning("interview_question_generation_failed: %s", exc)

        return _FALLBACK_QUESTIONS[:QUESTIONS_PER_SESSION]

    def _generate_feedback(self, role_title: str, question: str, answer: str) -> str:
        system_prompt = (
            "You are an experienced interview coach. Given an interview question and a "
            f"candidate's answer for a '{role_title}' role, give concise, constructive feedback "
            "(2-4 sentences): what was strong, what to improve, and one concrete suggestion to "
            "make the answer more compelling (e.g. using the STAR method, adding metrics, etc.). "
            "Respond with plain text feedback only, no JSON, no markdown."
        )
        user_prompt = f"Question: {question}\n\nCandidate's answer: {answer}"

        try:
            return self.llm.generate(system_prompt, user_prompt).strip()
        except Exception as exc:
            logger.warning("interview_feedback_generation_failed: %s", exc)
            return (
                "We couldn't generate AI feedback for this answer right now. "
                "Review your answer for clarity, structure (e.g. the STAR method), and specific examples."
            )


class InterviewSessionListItemData:
    """Small bundle pairing a session with its question/answer progress counts."""

    def __init__(self, session: InterviewSession, question_count: int, answered_count: int):
        self.session = session
        self.question_count = question_count
        self.answered_count = answered_count

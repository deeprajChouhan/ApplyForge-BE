"""
Interview Practice API routes.

  POST /interview/sessions              -> start a new mock interview session
  GET  /interview/sessions              -> list the user's sessions
  GET  /interview/sessions/{id}         -> session detail incl. questions/answers/feedback
  POST /interview/sessions/{id}/answers -> submit an answer, get AI feedback
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_feature
from app.db.session import get_db
from app.models.enums import FeatureFlag
from app.models.models import User
from app.schemas.interview import (
    InterviewAnswerOut,
    InterviewAnswerSubmit,
    InterviewSessionCreate,
    InterviewSessionListItem,
    InterviewSessionOut,
)
from app.services.interview.service import InterviewService

router = APIRouter(prefix="/interview", tags=["interview"])

_need_interview = Depends(require_feature(FeatureFlag.interview_practice))


def _session_out(service: InterviewService, session_id: int) -> InterviewSessionOut:
    session, answers = service.get_session_with_answers(session_id)
    return InterviewSessionOut(
        id=session.id,
        role_title=session.role_title,
        status=session.status,
        application_id=session.application_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        answers=[InterviewAnswerOut.model_validate(a) for a in answers],
    )


@router.post("/sessions", response_model=InterviewSessionOut, dependencies=[_need_interview])
def start_session(
    payload: InterviewSessionCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = InterviewService(db, user.id)
    session = service.start_session(payload.role_title, payload.application_id, request=request)
    return _session_out(service, session.id)


@router.get("/sessions", response_model=list[InterviewSessionListItem], dependencies=[_need_interview])
def list_sessions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = InterviewService(db, user.id).list_sessions()
    return [
        InterviewSessionListItem(
            id=item.session.id,
            role_title=item.session.role_title,
            status=item.session.status,
            application_id=item.session.application_id,
            question_count=item.question_count,
            answered_count=item.answered_count,
            created_at=item.session.created_at,
            updated_at=item.session.updated_at,
        )
        for item in items
    ]


@router.get("/sessions/{session_id}", response_model=InterviewSessionOut, dependencies=[_need_interview])
def get_session(session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = InterviewService(db, user.id)
    return _session_out(service, session_id)


@router.post("/sessions/{session_id}/answers", response_model=InterviewAnswerOut, dependencies=[_need_interview])
def submit_answer(
    session_id: int,
    payload: InterviewAnswerSubmit,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return InterviewService(db, user.id).submit_answer(session_id, payload.question_index, payload.answer, request=request)

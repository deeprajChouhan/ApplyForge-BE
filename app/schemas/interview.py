"""Pydantic schemas for mock interview practice sessions."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class InterviewSessionCreate(BaseModel):
    role_title: str
    application_id: Optional[int] = None


class InterviewAnswerOut(BaseModel):
    id: int
    question_index: int
    question: str
    answer: Optional[str] = None
    feedback: Optional[str] = None

    model_config = {"from_attributes": True}


class InterviewSessionOut(BaseModel):
    id: int
    role_title: str
    status: str
    application_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    answers: list[InterviewAnswerOut] = []

    model_config = {"from_attributes": True}


class InterviewSessionListItem(BaseModel):
    id: int
    role_title: str
    status: str
    application_id: Optional[int] = None
    question_count: int
    answered_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class InterviewAnswerSubmit(BaseModel):
    question_index: int
    answer: str

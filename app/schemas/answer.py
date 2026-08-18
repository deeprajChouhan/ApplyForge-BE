from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict

FieldTypeStr = Literal[
    "short_text",
    "long_text",
    "single_select",
    "multi_select",
    "boolean",
    "number",
    "date",
    "email",
    "phone",
    "url",
    "file",
    "eeoc",
]


class AnswerIn(BaseModel):
    question_text: str
    answer_text: str
    field_type: FieldTypeStr = "short_text"
    tags: Optional[str] = None
    confidence: Optional[float] = None


class AnswerUpdate(BaseModel):
    question_text: Optional[str] = None
    answer_text: Optional[str] = None
    field_type: Optional[FieldTypeStr] = None
    tags: Optional[str] = None
    confidence: Optional[float] = None
    source: Optional[str] = None


class AnswerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    question_key: str
    question_text: str
    answer_text: str
    field_type: FieldTypeStr
    tags: Optional[str] = None
    confidence: Optional[float] = None
    source: str
    times_used: int
    last_used_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class AnswerLookupIn(BaseModel):
    question_text: str
    field_type: Optional[FieldTypeStr] = None


class AnswerLookupOut(BaseModel):
    matched: bool
    answer_id: Optional[int] = None
    answer_text: Optional[str] = None
    confidence: float
    question_text: Optional[str] = None
    match_type: Literal["exact", "semantic", "none"]


class AnswerSeedResult(BaseModel):
    created: int
    skipped: int

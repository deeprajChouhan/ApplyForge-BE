from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict

ToneStr = Literal["professional", "casual", "technical", "executive"]
CoverLetterStyleStr = Literal["standard", "story", "bullet", "short"]


class KitIn(BaseModel):
    name: str
    base_resume_id: Optional[int] = None
    tone: ToneStr = "professional"
    cover_letter_style: CoverLetterStyleStr = "standard"
    default_answers_ref_json: Optional[List[int]] = None
    is_default: bool = False
    notes: Optional[str] = None


class KitUpdate(BaseModel):
    name: Optional[str] = None
    base_resume_id: Optional[int] = None
    tone: Optional[ToneStr] = None
    cover_letter_style: Optional[CoverLetterStyleStr] = None
    default_answers_ref_json: Optional[List[int]] = None
    is_default: Optional[bool] = None
    notes: Optional[str] = None


class KitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    base_resume_id: Optional[int] = None
    tone: ToneStr
    cover_letter_style: CoverLetterStyleStr
    default_answers_ref_json: Optional[List[int]] = None
    is_default: bool
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

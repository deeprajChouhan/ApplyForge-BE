from datetime import datetime
from pydantic import BaseModel


class ChatMessageCreate(BaseModel):
    content: str


class ChatMessageOut(BaseModel):
    id: int
    sender_role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}

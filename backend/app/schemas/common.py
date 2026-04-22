from datetime import datetime
from pydantic import BaseModel


class Timestamped(BaseModel):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

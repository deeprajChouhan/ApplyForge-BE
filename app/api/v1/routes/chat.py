from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.models import User
from app.schemas.chat import ChatMessageCreate
from app.services.chat.service import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/{application_id}/messages")
def send_message(application_id: int, payload: ChatMessageCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = ChatService(db, user.id)
    service.send(application_id, payload.content)
    return {"ok": True}


@router.get("/{application_id}/messages")
def history(application_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ChatService(db, user.id).history(application_id)

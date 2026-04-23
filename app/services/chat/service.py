from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import ApplicationChat, ApplicationChatMessage
from app.services.ai.exceptions import AIProviderError
from app.services.ai.factory import get_llm_provider
from app.services.rag.service import RAGService


class ChatService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id
        self.llm = get_llm_provider()

    def _get_chat(self, application_id: int) -> ApplicationChat:
        chat = self.db.query(ApplicationChat).filter_by(user_id=self.user_id, application_id=application_id).first()
        if not chat:
            chat = ApplicationChat(user_id=self.user_id, application_id=application_id)
            self.db.add(chat)
            self.db.commit()
            self.db.refresh(chat)
        return chat

    def send(self, application_id: int, content: str):
        chat = self._get_chat(application_id)
        self.db.add(ApplicationChatMessage(chat_id=chat.id, sender_role="user", content=content))
        evidence = "\n".join(c.content for c, _ in RAGService(self.db, self.user_id).search(content, top_k=4))
        try:
            reply = self.llm.generate("Answer grounded only.", f"Q:{content}\nEvidence:{evidence}")
        except AIProviderError as exc:
            raise HTTPException(status_code=503, detail="Chat provider is temporarily unavailable") from exc
        self.db.add(ApplicationChatMessage(chat_id=chat.id, sender_role="assistant", content=reply))
        self.db.commit()

    def history(self, application_id: int):
        chat = self._get_chat(application_id)
        return self.db.query(ApplicationChatMessage).filter_by(chat_id=chat.id).order_by(ApplicationChatMessage.id.asc()).all()

from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.models import GeneratedDocument


class DocumentService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    def get(self, doc_id: int) -> GeneratedDocument:
        doc = self.db.query(GeneratedDocument).filter_by(id=doc_id, user_id=self.user_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return doc

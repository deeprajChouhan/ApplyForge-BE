from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.models import User
from app.schemas.knowledge import KnowledgeSearchRequest
from app.services.rag.service import RAGService

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/reindex")
def reindex(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"chunks_indexed": RAGService(db, user.id).rebuild_index()}


@router.post("/search")
def search(payload: KnowledgeSearchRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = RAGService(db, user.id).search(payload.query, payload.top_k)
    return [{"chunk_id": c.id, "content": c.content, "score": score} for c, score in rows]

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.models import User
from app.services.documents.service import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/{doc_id}/download")
def download(doc_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    doc = DocumentService(db, user.id).get(doc_id)
    return PlainTextResponse(doc.content, headers={"Content-Disposition": f'attachment; filename="{doc.doc_type.value}_v{doc.version}.txt"'})

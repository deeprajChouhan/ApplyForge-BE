import json
from pathlib import Path
from pypdf import PdfReader
from docx import Document
from fastapi import UploadFile, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import FileType
from app.models.models import UploadedFile, ParsedResumeData


class ResumeParsingService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

    async def save_upload(self, upload: UploadFile) -> UploadedFile:
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in {".pdf", ".docx", ".txt"}:
            raise HTTPException(status_code=400, detail="Unsupported file type")
        upload_dir = Path(settings.upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        path = upload_dir / f"{self.user_id}_{upload.filename}"
        content = await upload.read()
        path.write_bytes(content)
        row = UploadedFile(
            user_id=self.user_id,
            file_type=FileType.resume,
            filename=upload.filename,
            content_type=upload.content_type,
            path=str(path),
            size_bytes=len(content),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def parse_resume(self, uploaded_file_id: int) -> ParsedResumeData:
        file = self.db.query(UploadedFile).filter_by(id=uploaded_file_id, user_id=self.user_id).first()
        if not file:
            raise HTTPException(status_code=404, detail="File not found")
        raw_text = self._extract_text(file.path)
        structured = {
            "summary": raw_text[:300],
            "skills": [s.strip() for s in ["Python", "FastAPI"] if s.lower() in raw_text.lower() or True],
        }
        parsed = ParsedResumeData(
            user_id=self.user_id,
            uploaded_file_id=file.id,
            raw_text=raw_text,
            structured_json=json.dumps(structured),
            confidence_score=0.65,
        )
        self.db.add(parsed)
        self.db.commit()
        self.db.refresh(parsed)
        return parsed

    def _extract_text(self, path: str) -> str:
        p = Path(path)
        if p.suffix.lower() == ".txt":
            return p.read_text(encoding="utf-8", errors="ignore")
        if p.suffix.lower() == ".pdf":
            return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        if p.suffix.lower() == ".docx":
            return "\n".join(paragraph.text for paragraph in Document(path).paragraphs)
        raise HTTPException(status_code=400, detail="Unsupported file type")

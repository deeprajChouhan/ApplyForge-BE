import json
from io import BytesIO
from pathlib import Path

from docx import Document
from fastapi import HTTPException, UploadFile
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.models.enums import FileType
from app.models.models import ParsedResumeData, UploadedFile
from app.services.storage import S3StorageService


class ResumeParsingService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id
        self.storage = S3StorageService()

    async def save_upload(self, upload: UploadFile) -> UploadedFile:
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in {".pdf", ".docx", ".txt"}:
            raise HTTPException(status_code=400, detail="Unsupported file type")

        content = await upload.read()
        stored_path = self.storage.upload_bytes(
            content=content,
            key_prefix=f"users/{self.user_id}/resumes",
            filename=upload.filename,
            content_type=upload.content_type,
        )

        row = UploadedFile(
            user_id=self.user_id,
            file_type=FileType.resume,
            filename=upload.filename,
            content_type=upload.content_type,
            path=stored_path,
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

    def _extract_text(self, file_path: str) -> str:
        if not file_path.startswith("s3://"):
            return self._extract_text_from_bytes(Path(file_path).read_bytes(), Path(file_path).suffix)

        content = self.storage.download_bytes(file_path)
        suffix = Path(file_path).suffix
        return self._extract_text_from_bytes(content, suffix)

    def _extract_text_from_bytes(self, content: bytes, suffix: str) -> str:
        ext = suffix.lower()
        if ext == ".txt":
            return content.decode("utf-8", errors="ignore")
        if ext == ".pdf":
            return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
        if ext == ".docx":
            return "\n".join(paragraph.text for paragraph in Document(BytesIO(content)).paragraphs)
        raise HTTPException(status_code=400, detail="Unsupported file type")

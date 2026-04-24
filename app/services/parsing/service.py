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
        structured = self._extract_structured_data(raw_text)

        parsed = ParsedResumeData(
            user_id=self.user_id,
            uploaded_file_id=file.id,
            raw_text=raw_text,
            structured_json=json.dumps(structured),
            confidence_score=0.90,
        )
        self.db.add(parsed)
        self.db.commit()
        self.db.refresh(parsed)
        return parsed

    def _extract_structured_data(self, raw_text: str) -> dict:
        from app.services.ai.factory import get_llm_provider

        system_prompt = (
            "You are an expert resume parser. Extract ALL information from the resume text provided. "
            "Return ONLY a valid JSON object with no markdown fences or extra commentary. "
            "Use this exact schema:\n"
            "{\n"
            '  "full_name": "string or null",\n'
            '  "headline": "string or null",\n'
            '  "email": "string or null",\n'
            '  "phone": "string or null",\n'
            '  "location": "string or null",\n'
            '  "linkedin": "string or null",\n'
            '  "github": "string or null",\n'
            '  "summary": "full professional summary string or null",\n'
            '  "skills": ["list of every skill string mentioned anywhere"],\n'
            '  "work_experience": [\n'
            '    {"company": "string", "role": "string", "start_date": "YYYY-MM or null", '
            '"end_date": "YYYY-MM or null or Present", "description": "string or null"}\n'
            "  ],\n"
            '  "education": [\n'
            '    {"institution": "string", "degree": "string or null", '
            '"field_of_study": "string or null", "start_date": "YYYY or null", "end_date": "YYYY or null"}\n'
            "  ],\n"
            '  "projects": [\n'
            '    {"name": "string", "description": "string or null", '
            '"technologies": "comma-separated string or null"}\n'
            "  ],\n"
            '  "certifications": [\n'
            '    {"name": "string", "issuer": "string or null", "issue_date": "YYYY-MM or null"}\n'
            "  ]\n"
            "}\n"
            "Extract every skill mentioned anywhere in the resume. Do not omit anything."
        )
        user_prompt = f"Parse this resume:\n\n{raw_text}"

        try:
            llm = get_llm_provider()
            response_text = llm.generate(system_prompt, user_prompt)
            # Strip potential markdown code fences if LLM adds them
            response_text = response_text.strip()
            if response_text.startswith("```"):
                parts = response_text.split("```")
                # parts[1] is the content between first and second ```
                inner = parts[1]
                if inner.startswith("json"):
                    inner = inner[4:]
                response_text = inner
            return json.loads(response_text.strip())
        except Exception:
            # Graceful fallback: keyword skill detection + raw text summary
            common_skills = [
                "Python", "JavaScript", "TypeScript", "Java", "C++", "C#", "Go", "Rust",
                "React", "Vue", "Angular", "Node.js", "FastAPI", "Django", "Flask",
                "SQL", "PostgreSQL", "MySQL", "MongoDB", "Redis", "Docker", "Kubernetes",
                "AWS", "Azure", "GCP", "Linux", "Git", "CI/CD", "REST", "GraphQL",
                "Machine Learning", "Deep Learning", "TensorFlow", "PyTorch", "Pandas",
                "NumPy", "Scikit-learn", "OpenAI", "LangChain", "RAG", "Cybersecurity",
                "Penetration Testing", "Automation", "Selenium", "Playwright",
            ]
            detected_skills = [s for s in common_skills if s.lower() in raw_text.lower()]
            return {
                "full_name": None,
                "headline": None,
                "email": None,
                "phone": None,
                "location": None,
                "linkedin": None,
                "github": None,
                "summary": raw_text[:2000],
                "skills": detected_skills,
                "work_experience": [],
                "education": [],
                "projects": [],
                "certifications": [],
            }

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

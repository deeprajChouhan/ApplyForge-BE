import json
from typing import List
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models.models import ApplicationStatusHistory, GeneratedDocument, JobApplication
from app.models.enums import ApplicationStatus
from app.services.ai.exceptions import AIProviderError
from app.services.ai.factory import get_llm_provider
from app.services.rag.service import RAGService


class ApplicationService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id
        self.llm = get_llm_provider()

    def create(self, payload: dict) -> JobApplication:
        app = JobApplication(user_id=self.user_id, **payload)
        self.db.add(app)
        self.db.flush()
        self.db.add(ApplicationStatusHistory(application_id=app.id, old_status=None, new_status=app.status))
        self.db.commit()
        self.db.refresh(app)
        return app

    def get(self, app_id: int) -> JobApplication:
        app = self.db.query(JobApplication).filter_by(id=app_id, user_id=self.user_id).first()
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")
        return app

    def update(self, app_id: int, payload: dict) -> JobApplication:
        app = self.get(app_id)
        for k, v in payload.items():
            if v is not None:
                setattr(app, k, v)
        self.db.commit()
        self.db.refresh(app)
        return app

    def list(self, status: ApplicationStatus | None = None):
        q = self.db.query(JobApplication).filter_by(user_id=self.user_id)
        if status:
            q = q.filter_by(status=status)
        return q.order_by(JobApplication.created_at.desc()).all()

    def change_status(self, app_id: int, new_status: ApplicationStatus, note: str | None) -> JobApplication:
        app = self.get(app_id)
        old_status = app.status
        app.status = new_status
        self.db.add(ApplicationStatusHistory(application_id=app.id, old_status=old_status, new_status=new_status, note=note))
        self.db.commit()
        self.db.refresh(app)
        return app

    def analyze_jd(self, app_id: int, jd: str) -> dict:
        rag = RAGService(self.db, self.user_id)
        evidence = [c.content for c, _ in rag.search(jd, top_k=5)]
        result = {
            "keywords": jd.split()[:10],
            "required_skills": [],
            "preferred_skills": [],
            "strengths": ["Evidence-backed profile alignment"] if evidence else [],
            "unsupported_gaps": ["Explicitly mark missing requirements manually"],
            "fit_summary": "Preliminary fit summary based on user evidence only.",
        }
        app = self.get(app_id)
        app.jd_analysis_json = json.dumps(result)
        self.db.commit()
        return result

    def generate_docs(self, app_id: int, doc_types: List) -> List[GeneratedDocument]:
        app = self.get(app_id)
        rag = RAGService(self.db, self.user_id)
        evidence = "\n".join(c.content for c, _ in rag.search(app.job_description, top_k=6))
        out = []
        for dt in doc_types:
            prior = self.db.query(GeneratedDocument).filter_by(application_id=app.id, doc_type=dt).count()
            prompt = f"Generate {dt.value} using ONLY evidence:\n{evidence}\nJD:\n{app.job_description}"
            try:
                text = self.llm.generate("Truthful generation only", prompt)
            except AIProviderError as exc:
                raise HTTPException(status_code=503, detail="Document generation provider is unavailable") from exc
            row = GeneratedDocument(
                user_id=self.user_id,
                application_id=app.id,
                doc_type=dt,
                version=prior + 1,
                content=text,
                format="txt",
            )
            self.db.add(row)
            out.append(row)
        self.db.commit()
        return out

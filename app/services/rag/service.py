import json
import math
from sqlalchemy.orm import Session

from app.models.models import KnowledgeChunk, KnowledgeDocument, UserProfile, WorkExperience, Skill
from app.services.ai.factory import get_embedding_provider


class RAGService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id
        self.embedder = get_embedding_provider()

    def rebuild_index(self) -> int:
        self.db.query(KnowledgeChunk).filter_by(user_id=self.user_id).delete()
        self.db.query(KnowledgeDocument).filter_by(user_id=self.user_id).delete()

        docs = self._compose_documents()
        chunk_count = 0
        for source_type, content in docs:
            doc = KnowledgeDocument(user_id=self.user_id, source_type=source_type, content=content)
            self.db.add(doc)
            self.db.flush()
            for idx, chunk in enumerate(self._chunk(content)):
                emb = self.embedder.embed(chunk)
                self.db.add(KnowledgeChunk(user_id=self.user_id, document_id=doc.id, chunk_index=idx, content=chunk, embedding=json.dumps(emb)))
                chunk_count += 1
        self.db.commit()
        return chunk_count

    def search(self, query: str, top_k: int = 5):
        q_emb = self.embedder.embed(query)
        chunks = self.db.query(KnowledgeChunk).filter_by(user_id=self.user_id).all()
        scored = []
        for c in chunks:
            score = self._cosine(q_emb, json.loads(c.embedding))
            scored.append((c, score))
        return sorted(scored, key=lambda x: x[1], reverse=True)[:top_k]

    def _compose_documents(self):
        out = []
        prof = self.db.query(UserProfile).filter_by(user_id=self.user_id).first()
        if prof:
            out.append(("profile", f"{prof.full_name or ''}\n{prof.summary or ''}"))
        for e in self.db.query(WorkExperience).filter_by(user_id=self.user_id).all():
            out.append(("experience", f"{e.role} at {e.company}. {e.description or ''}"))
        skills = [s.name for s in self.db.query(Skill).filter_by(user_id=self.user_id).all()]
        if skills:
            out.append(("skills", ", ".join(skills)))
        return out

    def _chunk(self, text: str, chunk_size: int = 500):
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)] or [""]

    def _cosine(self, a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb + 1e-9)

import json
import math
from sqlalchemy.orm import Session

from app.models.models import (
    Certification, Education, KnowledgeChunk, KnowledgeDocument,
    ParsedResumeData, Project, Skill, UserProfile, WorkExperience,
)
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
            if not content.strip():
                continue
            doc = KnowledgeDocument(user_id=self.user_id, source_type=source_type, content=content)
            self.db.add(doc)
            self.db.flush()
            for idx, chunk in enumerate(self._chunk(content)):
                emb = self.embedder.embed(chunk)
                self.db.add(KnowledgeChunk(
                    user_id=self.user_id,
                    document_id=doc.id,
                    chunk_index=idx,
                    content=chunk,
                    embedding=json.dumps(emb),
                ))
                chunk_count += 1
        self.db.commit()
        return chunk_count

    def search(self, query: str, top_k: int = 5):
        q_emb = self.embedder.embed(query)
        chunks = self.db.query(KnowledgeChunk).filter_by(user_id=self.user_id).all()
        scored = [(c, self._cosine(q_emb, json.loads(c.embedding))) for c in chunks]
        return sorted(scored, key=lambda x: x[1], reverse=True)[:top_k]

    # ── Document composition ───────────────────────────────────────────────

    def _compose_documents(self) -> list[tuple[str, str]]:
        """
        Build knowledge documents from BOTH the structured profile tables AND
        the latest parsed resume. Profile tables take priority; parsed resume
        fills gaps (e.g. user uploaded a resume but never filled the profile form).
        """
        out: list[tuple[str, str]] = []

        # ── 1. Structured profile sections ────────────────────────────────
        prof = self.db.query(UserProfile).filter_by(user_id=self.user_id).first()
        if prof and (prof.full_name or prof.summary or prof.headline):
            parts = []
            if prof.full_name: parts.append(f"Name: {prof.full_name}")
            if prof.headline:  parts.append(f"Headline: {prof.headline}")
            if prof.location:  parts.append(f"Location: {prof.location}")
            if prof.summary:   parts.append(f"Summary: {prof.summary}")
            out.append(("profile", "\n".join(parts)))

        # Work experience
        exps = self.db.query(WorkExperience).filter_by(user_id=self.user_id).all()
        for e in exps:
            start = str(e.start_date) if e.start_date else ""
            end   = str(e.end_date)   if e.end_date   else "Present"
            date_range = f"{start} – {end}" if start else end
            text = f"{e.role} at {e.company} ({date_range})."
            if e.description:
                text += f"\n{e.description}"
            out.append(("experience", text))

        # Education
        edus = self.db.query(Education).filter_by(user_id=self.user_id).all()
        for ed in edus:
            parts = [f"{ed.degree or 'Degree'} in {ed.field_of_study or 'N/A'} at {ed.institution}"]
            if ed.start_date or ed.end_date:
                parts.append(f"({ed.start_date or ''} – {ed.end_date or 'Present'})")
            out.append(("education", " ".join(parts)))

        # Skills from DB — queried here so we can merge with parsed resume skills below
        db_skills = self.db.query(Skill).filter_by(user_id=self.user_id).all()

        # Projects
        projs = self.db.query(Project).filter_by(user_id=self.user_id).all()
        for p in projs:
            text = f"Project: {p.name}"
            if p.technologies: text += f" | Tech: {p.technologies}"
            if p.description:  text += f"\n{p.description}"
            out.append(("project", text))

        # Certifications
        certs = self.db.query(Certification).filter_by(user_id=self.user_id).all()
        if certs:
            cert_lines = []
            for c in certs:
                line = c.name
                if c.issuer:      line += f" — {c.issuer}"
                if c.issue_date:  line += f" ({c.issue_date})"
                cert_lines.append(line)
            out.append(("certifications", "Certifications:\n" + "\n".join(cert_lines)))

        # ── 2. Parsed resume (fills gaps; always included as extra signal) ─
        parsed = (
            self.db.query(ParsedResumeData)
            .filter_by(user_id=self.user_id)
            .order_by(ParsedResumeData.created_at.desc())
            .first()
        )
        if parsed:
            # Always add raw resume text as a high-recall fallback chunk
            if parsed.raw_text:
                out.append(("resume_raw", parsed.raw_text[:4000]))

            # Also unpack structured_json to add any sections not in profile tables
            try:
                rd = json.loads(parsed.structured_json)

                # Skills: merge DB skills + parsed resume skills (deduplicated)
                seen = {sk.name.lower().strip() for sk in db_skills}
                extra_resume_skills = [rs for rs in rd.get("skills", []) if rs.lower().strip() not in seen]

                all_skill_parts = (
                    [f"{sk.name} ({sk.level})" if sk.level else sk.name for sk in db_skills]
                    + extra_resume_skills
                )
                if all_skill_parts:
                    out.append(("skills", "Skills: " + ", ".join(all_skill_parts)))

                # Experience from parsed resume (if profile experience table is empty)
                if not exps and rd.get("work_experience"):
                    for e in rd["work_experience"]:
                        text = f"{e.get('role','')} at {e.get('company','')} ({e.get('start_date','')} – {e.get('end_date','Present')})."
                        if e.get("description"): text += f"\n{e['description']}"
                        out.append(("resume_experience", text))

                # Education from parsed resume (if empty)
                if not edus and rd.get("education"):
                    for e in rd["education"]:
                        out.append(("resume_education",
                            f"{e.get('degree','')} in {e.get('field_of_study','')} at {e.get('institution','')} "
                            f"({e.get('start_date','')} – {e.get('end_date','')})"))

                # Projects from parsed resume (if empty)
                if not projs and rd.get("projects"):
                    for p in rd["projects"]:
                        text = f"Project: {p.get('name','')}"
                        if p.get("technologies"): text += f" | Tech: {p['technologies']}"
                        if p.get("description"):  text += f"\n{p['description']}"
                        out.append(("resume_project", text))

                # Certifications from parsed resume (if empty)
                if not certs and rd.get("certifications"):
                    cert_lines = [
                        f"{c.get('name','')} — {c.get('issuer','')} ({c.get('issue_date','')})"
                        for c in rd["certifications"]
                    ]
                    out.append(("resume_certifications", "Certifications:\n" + "\n".join(cert_lines)))

                # Contact info from resume (phone, LinkedIn, GitHub — not in DB schema)
                contact_parts = []
                if rd.get("phone"):    contact_parts.append(f"Phone: {rd['phone']}")
                if rd.get("linkedin"): contact_parts.append(f"LinkedIn: {rd['linkedin']}")
                if rd.get("github"):   contact_parts.append(f"GitHub: {rd['github']}")
                if contact_parts:
                    out.append(("resume_contact", "\n".join(contact_parts)))

            except (json.JSONDecodeError, TypeError):
                pass  # structured_json malformed — raw_text already added above

        return out


    # ── Helpers ────────────────────────────────────────────────────────────

    def _chunk(self, text: str, chunk_size: int = 500) -> list[str]:
        return [text[i: i + chunk_size] for i in range(0, len(text), chunk_size)] or [""]

    def _cosine(self, a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb + 1e-9)

import json
import re
from typing import List
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models.models import (
    ApplicationChat, ApplicationStatusHistory, Certification, Education, GeneratedDocument,
    JobApplication, Project, Skill, User, UserProfile, WorkExperience,
)
from app.models.enums import ApplicationStatus
from app.services.ai.exceptions import AIProviderError
from app.services.ai.factory import get_llm_provider
from app.services.rag.service import RAGService


class ApplicationService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id
        self.llm = get_llm_provider()

    # ── CRUD ──────────────────────────────────────────────────────────────

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

    def delete(self, app_id: int) -> None:
        """Permanently delete an application + all related data (docs, chat, status history)."""
        app = self.get(app_id)  # raises 404 if not found / wrong user
        self.db.query(GeneratedDocument).filter_by(application_id=app_id).delete()
        self.db.query(ApplicationChat).filter_by(application_id=app_id).delete()
        self.db.query(ApplicationStatusHistory).filter_by(application_id=app_id).delete()
        self.db.delete(app)
        self.db.commit()

    # ── Profile builder ───────────────────────────────────────────────────

    def _build_structured_profile(self) -> str:
        """
        Build structured profile block for LLM resume generation.
        Priority: DB profile tables > latest parsed resume structured_json (fallback).
        Guarantees real data, no [placeholder] gaps regardless of how the user added data.
        """
        from app.models.models import ParsedResumeData

        user    = self.db.query(User).filter_by(id=self.user_id).first()
        profile = self.db.query(UserProfile).filter_by(user_id=self.user_id).first()
        exps    = self.db.query(WorkExperience).filter_by(user_id=self.user_id).order_by(WorkExperience.start_date.desc()).all()
        edus    = self.db.query(Education).filter_by(user_id=self.user_id).order_by(Education.start_date.desc()).all()
        skills  = self.db.query(Skill).filter_by(user_id=self.user_id).all()
        projs   = self.db.query(Project).filter_by(user_id=self.user_id).all()
        certs   = self.db.query(Certification).filter_by(user_id=self.user_id).all()

        # Parsed resume as fallback for any missing sections
        parsed = (
            self.db.query(ParsedResumeData)
            .filter_by(user_id=self.user_id)
            .order_by(ParsedResumeData.created_at.desc())
            .first()
        )
        rd: dict = {}
        if parsed:
            try:
                rd = json.loads(parsed.structured_json) or {}
            except (json.JSONDecodeError, TypeError):
                rd = {}

        lines: list[str] = []

        # Contact — merge profile DB + parsed resume
        lines.append("=== CONTACT INFORMATION ===")
        name     = (profile.full_name if profile and profile.full_name else None) or rd.get("full_name") or "Not provided"
        email    = (user.email        if user                           else None) or rd.get("email")    or "Not provided"
        location = (profile.location  if profile and profile.location   else None) or rd.get("location") or ""
        headline = (profile.headline  if profile and profile.headline   else None) or rd.get("headline") or ""
        phone    = rd.get("phone")    or ""
        linkedin = rd.get("linkedin") or ""
        github   = rd.get("github")   or ""
        lines.append(f"Name:     {name}")
        lines.append(f"Email:    {email}")
        if location: lines.append(f"Location: {location}")
        if headline: lines.append(f"Headline: {headline}")
        if phone:    lines.append(f"Phone:    {phone}")
        if linkedin: lines.append(f"LinkedIn: {linkedin}")
        if github:   lines.append(f"GitHub:   {github}")

        # Summary
        summary = (profile.summary if profile and profile.summary else None) or rd.get("summary")
        if summary:
            lines.append("\n=== PROFESSIONAL SUMMARY ===")
            lines.append(summary)

        # Work Experience: DB first, parsed resume as fallback
        if exps:
            lines.append("\n=== WORK EXPERIENCE ===")
            for e in exps:
                lines.append(f"Role:    {e.role}")
                lines.append(f"Company: {e.company}")
                lines.append(f"Period:  {e.start_date or 'Unknown'} – {e.end_date or 'Present'}")
                if e.description: lines.append(f"Details:\n{e.description}")
                lines.append("")
        elif rd.get("work_experience"):
            lines.append("\n=== WORK EXPERIENCE ===")
            for e in rd["work_experience"]:
                lines.append(f"Role:    {e.get('role', '')}")
                lines.append(f"Company: {e.get('company', '')}")
                lines.append(f"Period:  {e.get('start_date', '')} – {e.get('end_date', 'Present')}")
                if e.get("description"): lines.append(f"Details:\n{e['description']}")
                lines.append("")

        # Education: DB first, parsed resume as fallback
        if edus:
            lines.append("=== EDUCATION ===")
            for ed in edus:
                start  = str(ed.start_date) if ed.start_date else ""
                end    = str(ed.end_date)   if ed.end_date   else "Present"
                lines.append(f"Institution: {ed.institution}")
                lines.append(f"Degree:      {ed.degree or 'N/A'} in {ed.field_of_study or 'N/A'}")
                lines.append(f"Period:      {start} – {end}")
                lines.append("")
        elif rd.get("education"):
            lines.append("=== EDUCATION ===")
            for e in rd["education"]:
                lines.append(f"Institution: {e.get('institution', '')}")
                lines.append(f"Degree:      {e.get('degree', 'N/A')} in {e.get('field_of_study', 'N/A')}")
                lines.append(f"Period:      {e.get('start_date', '')} – {e.get('end_date', '')}")
                lines.append("")

        # Skills: ALWAYS merge DB profile skills + parsed resume skills (deduplicated)
        merged_skill_names: list[str] = []
        seen_skills: set[str] = set()

        # DB skills first (they have level info)
        db_skill_parts: list[str] = []
        for s in skills:
            key = s.name.lower().strip()
            if key not in seen_skills:
                seen_skills.add(key)
                db_skill_parts.append(f"{s.name} ({s.level})" if s.level else s.name)
                merged_skill_names.append(s.name)

        # Then add any extra skills from parsed resume not already present in DB
        resume_extra_skills: list[str] = []
        for rs in rd.get("skills", []):
            key = rs.lower().strip()
            if key not in seen_skills:
                seen_skills.add(key)
                resume_extra_skills.append(rs)
                merged_skill_names.append(rs)

        all_skill_parts = db_skill_parts + resume_extra_skills
        if all_skill_parts:
            lines.append("=== SKILLS ===")
            lines.append(", ".join(all_skill_parts))

        # Projects: DB first, parsed resume as fallback
        if projs:
            lines.append("\n=== PROJECTS ===")
            for p in projs:
                lines.append(f"Project: {p.name}")
                if p.technologies: lines.append(f"Tech:    {p.technologies}")
                if p.description:  lines.append(p.description)
                lines.append("")
        elif rd.get("projects"):
            lines.append("\n=== PROJECTS ===")
            for p in rd["projects"]:
                lines.append(f"Project: {p.get('name', '')}")
                if p.get("technologies"): lines.append(f"Tech:    {p['technologies']}")
                if p.get("description"):  lines.append(p["description"])
                lines.append("")

        # Certifications: DB first, parsed resume as fallback
        if certs:
            lines.append("=== CERTIFICATIONS ===")
            for c in certs:
                lines.append(f"• {c.name}{f' — {c.issuer}' if c.issuer else ''}{f' ({c.issue_date})' if c.issue_date else ''}")
        elif rd.get("certifications"):
            lines.append("=== CERTIFICATIONS ===")
            for c in rd["certifications"]:
                cname   = c.get("name", "")
                cissuer = f" \u2014 {c['issuer']}" if c.get("issuer") else ""
                cdate   = f" ({c['issue_date']})" if c.get("issue_date") else ""
                lines.append(f"\u2022 {cname}{cissuer}{cdate}")

        return "\n".join(lines)

    # ── JD Analysis ───────────────────────────────────────────────────────

    def analyze_jd(self, app_id: int, jd: str) -> dict:
        rag = RAGService(self.db, self.user_id)
        evidence_chunks = [c.content for c, _ in rag.search(jd, top_k=6)]
        evidence_text = "\n\n".join(evidence_chunks) if evidence_chunks else "No profile evidence available."

        system_prompt = (
            "You are an expert recruiter and career coach. Analyze the job description against the candidate's profile evidence. "
            "Return ONLY a valid JSON object with no markdown fences. Use this exact schema:\n"
            "{\n"
            '  "keywords": ["top 10 important keywords/phrases from the JD"],\n'
            '  "required_skills": ["explicitly required skills listed in the JD"],\n'
            '  "preferred_skills": ["nice-to-have or preferred skills from the JD"],\n'
            '  "strengths": ["areas where the candidate profile clearly matches the JD requirements"],\n'
            '  "unsupported_gaps": ["requirements in the JD that the candidate profile does NOT clearly support"],\n'
            '  "fit_summary": "2-3 sentence overall fit assessment explaining how well the candidate matches this role"\n'
            "}"
        )
        user_prompt = (
            f"=== JOB DESCRIPTION ===\n{jd}\n\n"
            f"=== CANDIDATE PROFILE EVIDENCE ===\n{evidence_text}"
        )

        try:
            response_text = self.llm.generate(system_prompt, user_prompt).strip()
            if response_text.startswith("```"):
                parts = response_text.split("```")
                inner = parts[1]
                if inner.startswith("json"):
                    inner = inner[4:]
                response_text = inner
            result = json.loads(response_text.strip())
        except Exception:
            words = re.findall(r'\b[A-Za-z][A-Za-z+#.]{2,}\b', jd)
            freq: dict[str, int] = {}
            stopwords = {'the', 'and', 'for', 'are', 'with', 'this', 'that', 'have', 'will', 'you', 'our', 'they', 'from', 'your'}
            for w in words:
                wl = w.lower()
                if wl not in stopwords:
                    freq[wl] = freq.get(wl, 0) + 1
            keywords = [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:10]]
            result = {
                "keywords": keywords,
                "required_skills": [],
                "preferred_skills": [],
                "strengths": ["Profile evidence retrieved — manual review recommended"] if evidence_chunks else [],
                "unsupported_gaps": ["LLM analysis unavailable — please retry"],
                "fit_summary": "Automated analysis could not be completed. Please retry or review manually.",
            }

        app = self.get(app_id)
        app.jd_analysis_json = json.dumps(result)
        self.db.commit()
        return result

    # ── Document generation ───────────────────────────────────────────────

    def generate_docs(self, app_id: int, doc_types: List) -> List[GeneratedDocument]:
        # Phase 1: Fetch all needed data from DB quickly
        app = self.get(app_id)
        job_description = app.job_description
        application_id  = app.id

        # RAG evidence for non-resume docs
        rag = RAGService(self.db, self.user_id)
        evidence = "\n\n".join(c.content for c, _ in rag.search(job_description, top_k=8))
        evidence_block = evidence if evidence else "No profile evidence found. Use general best practices."

        # Full structured profile for the resume (real data, no placeholders)
        structured_profile = self._build_structured_profile()

        # Version counts
        prior_counts = {
            dt: self.db.query(GeneratedDocument).filter_by(application_id=application_id, doc_type=dt).count()
            for dt in doc_types
        }

        # Release session — return connection to pool before LLM calls
        self.db.expire_all()

        # Phase 2: LLM calls (no DB connection held)
        RESUME_SYSTEM = (
            "You are an expert ATS-optimised resume writer with 15+ years of experience. "
            "Your task is to produce a clean, ATS-friendly resume using ONLY the candidate data provided below. "
            "\n\nATS RULES (follow strictly):\n"
            "• Use plain text only — NO tables, NO columns, NO text boxes, NO graphics, NO headers/footers\n"
            "• Use standard section headings: CONTACT, PROFESSIONAL SUMMARY, SKILLS, WORK EXPERIENCE, EDUCATION, PROJECTS, CERTIFICATIONS\n"
            "• Use simple bullet points starting with '•' for achievements\n"
            "• Start each bullet with a strong action verb (Led, Built, Designed, Improved, etc.)\n"
            "• Include quantifiable achievements wherever the evidence provides numbers/metrics\n"
            "• Mirror keywords from the job description naturally throughout the resume\n"
            "• Dates must be in format: Month YYYY – Month YYYY (or Present)\n"
            "• DO NOT invent any information not present in the candidate data\n"
            "• DO NOT use placeholders like [Your Email] — use only the exact data provided\n"
            "• Output clean text with clear section breaks using '---' between sections"
        )

        def resume_prompt(profile: str, jd: str) -> str:
            return (
                f"=== CANDIDATE PROFILE DATA ===\n{profile}\n\n"
                f"=== TARGET JOB DESCRIPTION ===\n{jd}\n\n"
                "Write a complete ATS-optimised resume using the EXACT candidate data above. "
                "Mirror keywords from the JD. Use the real email and location from the profile — never write placeholders."
            )

        doc_prompts: dict[str, tuple] = {
            "resume": (RESUME_SYSTEM, resume_prompt),
            "cover_letter": (
                "You are an expert cover letter writer. "
                "Write a compelling, personalised cover letter that connects the candidate's background to this specific role. "
                "Structure: Opening hook, 2-3 body paragraphs matching skills to JD requirements, strong closing. "
                "Tone: Professional but authentic. Length: 3-4 paragraphs. "
                "Use ONLY the provided evidence — do not fabricate experience.",
                lambda ev, jd: (
                    f"=== CANDIDATE EVIDENCE ===\n{ev}\n\n"
                    f"=== JOB DESCRIPTION ===\n{jd}\n\n"
                    "Write a tailored cover letter for this application."
                )
            ),
            "cold_email": (
                "You are a professional communication coach. Write a polite, concise cold/follow-up email for a job application. "
                "Keep it under 200 words. Express genuine interest, reference 1-2 specific things about the role, "
                "and ask for next steps respectfully. Include a subject line at the top.",
                lambda ev, jd: (
                    f"=== CANDIDATE EVIDENCE ===\n{ev}\n\n"
                    f"=== JOB DESCRIPTION ===\n{jd}\n\n"
                    "Write a cold outreach email for this job application."
                )
            ),
            "cold_message": (
                "You are a networking expert. Write a concise, compelling LinkedIn DM to a recruiter or hiring manager. "
                "Keep it under 300 words. Be specific to the role. Open with a personalised hook, "
                "mention 1-2 relevant strengths, end with a clear call to action. Do not be generic.",
                lambda ev, jd: (
                    f"=== CANDIDATE EVIDENCE ===\n{ev}\n\n"
                    f"=== JOB DESCRIPTION ===\n{jd}\n\n"
                    "Write a LinkedIn cold message for this role."
                )
            ),
        }

        generated_texts: dict[str, str] = {}
        for dt in doc_types:
            dt_key = dt.value if hasattr(dt, "value") else str(dt)
            entry = doc_prompts.get(dt_key)
            if not entry:
                system_prompt = "You are a professional career document writer. Generate high-quality content using ONLY the provided evidence."
                user_prompt   = f"=== CANDIDATE EVIDENCE ===\n{evidence_block}\n\n=== JOB DESCRIPTION ===\n{job_description}\n\nGenerate the document."
            else:
                system_prompt, build_prompt = entry
                # Resume uses structured_profile; others use RAG evidence
                if dt_key == "resume":
                    user_prompt = build_prompt(structured_profile, job_description)
                else:
                    user_prompt = build_prompt(evidence_block, job_description)
            try:
                generated_texts[dt_key] = self.llm.generate(system_prompt, user_prompt)
            except AIProviderError as exc:
                raise HTTPException(status_code=503, detail="Document generation provider is unavailable") from exc

        # Phase 3: Persist all results with a fresh DB round-trip
        out = []
        for dt in doc_types:
            dt_key = dt.value if hasattr(dt, "value") else str(dt)
            row = GeneratedDocument(
                user_id=self.user_id,
                application_id=application_id,
                doc_type=dt,
                version=prior_counts.get(dt, 0) + 1,
                content=generated_texts.get(dt_key, ""),
                format="txt",
            )
            self.db.add(row)
            out.append(row)

        self.db.commit()
        for row in out:
            self.db.refresh(row)
        return out

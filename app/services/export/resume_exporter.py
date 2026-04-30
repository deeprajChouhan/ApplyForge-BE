"""
Resume Export Service
=====================
Generates downloadable PDF and DOCX versions of the user's resume,
sourced from the normalised profile tables (same data the ATS preview
renders). Falls back to ParsedResumeData for any section that has no
DB rows yet (matching the frontend merge logic).

Libraries used:
  PDF  — reportlab  (already installed)
  DOCX — python-docx (already installed)
"""
from __future__ import annotations

import io
import json
from datetime import date, datetime
from typing import Any

import structlog
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import (
    Certification,
    Education,
    ParsedResumeData,
    Project,
    Skill,
    User,
    UserProfile,
    WorkExperience,
)

logger = structlog.get_logger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────

def _fmt_date(d: date | datetime | str | None) -> str:
    if d is None:
        return "Present"
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d)
        except ValueError:
            return d
    return d.strftime("%b %Y")


def _bullets(description: str | None, max_bullets: int = 6) -> list[str]:
    """Split a description text into bullet lines."""
    if not description:
        return []
    lines = [l.strip().lstrip("•–-").strip() for l in description.splitlines()]
    lines = [l for l in lines if l]
    if len(lines) > 1:
        return lines[:max_bullets]
    # single paragraph — split on ". "
    parts = [s.strip() for s in description.split(". ") if s.strip()]
    return parts[:max_bullets]


# ── data loader ────────────────────────────────────────────────────────────

class _ResumeData:
    """Loads all profile sections for a user, merging DB + parsed fallback."""

    def __init__(self, db: Session, user_id: int, user_email: str):
        profile = db.query(UserProfile).filter_by(user_id=user_id).first()
        parsed_row = (
            db.query(ParsedResumeData)
            .filter_by(user_id=user_id)
            .order_by(ParsedResumeData.created_at.desc())
            .first()
        )
        parsed: dict[str, Any] = {}
        if parsed_row and parsed_row.structured_json:
            try:
                raw = json.loads(parsed_row.structured_json)
                parsed = raw if isinstance(raw, dict) else {}
            except (json.JSONDecodeError, TypeError):
                parsed = {}

        # Basic info
        self.full_name: str = (
            (profile.full_name if profile else None)
            or parsed.get("full_name", "")
            or ""
        )
        self.headline: str = (
            (profile.headline if profile else None)
            or parsed.get("headline", "")
            or ""
        )
        self.summary: str = (
            (profile.summary if profile else None)
            or parsed.get("summary", "")
            or ""
        )
        self.location: str = (
            (profile.location if profile else None)
            or parsed.get("location", "")
            or ""
        )
        self.email: str = user_email

        # Skills — merge DB + parsed (same dedup logic as frontend)
        db_skills = db.query(Skill).filter_by(user_id=user_id).all()
        seen: set[str] = set()
        skills: list[dict] = []
        for s in db_skills:
            key = (s.name or "").lower().strip()
            if key and key not in seen:
                seen.add(key)
                skills.append({"name": s.name, "level": s.level})
        for s in parsed.get("skills", []):
            item = {"name": s} if isinstance(s, str) else s
            key = (item.get("name", "") or "").lower().strip()
            if key and key not in seen:
                seen.add(key)
                skills.append(item)
        self.skills = skills

        # Experiences
        db_exps = (
            db.query(WorkExperience)
            .filter_by(user_id=user_id)
            .order_by(WorkExperience.start_date.desc())
            .all()
        )
        if db_exps:
            self.experiences = [
                {
                    "role": e.role,
                    "company": e.company,
                    "start_date": e.start_date,
                    "end_date": e.end_date,
                    "description": e.description,
                }
                for e in db_exps
            ]
        else:
            raw_exps = parsed.get("work_experience", [])
            self.experiences = sorted(
                raw_exps,
                key=lambda x: x.get("start_date") or "",
                reverse=True,
            )

        # Education
        db_edus = (
            db.query(Education)
            .filter_by(user_id=user_id)
            .order_by(Education.start_date.desc())
            .all()
        )
        if db_edus:
            self.educations = [
                {
                    "institution": e.institution,
                    "degree": e.degree,
                    "field_of_study": e.field_of_study,
                    "start_date": e.start_date,
                    "end_date": e.end_date,
                }
                for e in db_edus
            ]
        else:
            self.educations = parsed.get("education", [])

        # Projects
        db_projs = db.query(Project).filter_by(user_id=user_id).all()
        if db_projs:
            self.projects = [
                {
                    "name": p.name,
                    "description": p.description,
                    "technologies": p.technologies,
                }
                for p in db_projs
            ]
        else:
            self.projects = parsed.get("projects", [])

        # Certifications
        db_certs = db.query(Certification).filter_by(user_id=user_id).all()
        if db_certs:
            self.certifications = [
                {"name": c.name, "issuer": c.issuer, "issue_date": c.issue_date}
                for c in db_certs
            ]
        else:
            self.certifications = parsed.get("certifications", [])


# ── PDF export ─────────────────────────────────────────────────────────────

def _build_pdf(data: _ResumeData) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )

    # ── colour palette ──────────────────────────────────────────────────
    ACCENT = colors.HexColor("#1e40af")   # blue-800
    DARK   = colors.HexColor("#111827")   # gray-900
    MID    = colors.HexColor("#374151")   # gray-700
    LIGHT  = colors.HexColor("#6b7280")   # gray-500

    # ── styles ──────────────────────────────────────────────────────────
    styles = getSampleStyleSheet()

    def S(name, **kw) -> ParagraphStyle:
        base = styles["Normal"]
        return ParagraphStyle(name, parent=base, **kw)

    name_style    = S("Name",    fontSize=22, fontName="Helvetica-Bold", textColor=DARK,   spaceAfter=2, leading=28)
    head_style    = S("Head",    fontSize=11, fontName="Helvetica",       textColor=ACCENT, spaceAfter=2, leading=16)
    contact_style = S("Contact", fontSize=9,  fontName="Helvetica",       textColor=LIGHT,  spaceAfter=6)
    summary_style = S("Summary", fontSize=9.5, fontName="Helvetica",      textColor=MID,    spaceAfter=4, leading=14)
    sec_style     = S("Section", fontSize=9,  fontName="Helvetica-Bold",  textColor=ACCENT, spaceBefore=8, spaceAfter=3,
                       textTransform="uppercase", letterSpacing=0.8)
    job_title_s   = S("JTitle",  fontSize=10.5, fontName="Helvetica-Bold", textColor=DARK)
    company_s     = S("Co",      fontSize=10,   fontName="Helvetica-Oblique", textColor=ACCENT)
    date_s        = S("Date",    fontSize=9,    fontName="Helvetica",      textColor=LIGHT, alignment=2)  # right
    bullet_s      = S("Bullet",  fontSize=9.5,  fontName="Helvetica",      textColor=MID,   leftIndent=10, spaceAfter=1, leading=13,
                       bulletIndent=0, bulletText="•")
    normal_s      = S("Normal2", fontSize=9.5,  fontName="Helvetica",      textColor=MID,   leading=13)

    def hr():
        return HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"), spaceAfter=4, spaceBefore=0)

    story = []

    # ── header ──────────────────────────────────────────────────────────
    story.append(Paragraph(data.full_name or "Resume", name_style))
    if data.headline:
        story.append(Paragraph(data.headline, head_style))
    contact_parts = []
    if data.email:
        contact_parts.append(data.email)
    if data.location:
        contact_parts.append(data.location)
    if contact_parts:
        story.append(Paragraph("  ·  ".join(contact_parts), contact_style))
    story.append(hr())

    # ── summary ─────────────────────────────────────────────────────────
    if data.summary:
        story.append(Paragraph("Professional Summary", sec_style))
        story.append(Paragraph(data.summary, summary_style))
        story.append(hr())

    # ── skills ──────────────────────────────────────────────────────────
    if data.skills:
        story.append(Paragraph("Skills", sec_style))

        # Group by level so rated skills cluster together
        by_level: dict[str, list[str]] = {}
        for s in data.skills:
            lvl = (s.get("level") or "").strip().lower() or "general"
            by_level.setdefault(lvl, []).append(s["name"])

        # Preferred display order for levels
        level_order = ["expert", "advanced", "intermediate", "beginner", "general"]
        ordered_levels = sorted(
            by_level.keys(),
            key=lambda l: level_order.index(l) if l in level_order else 99,
        )

        # Level label map
        level_labels = {
            "expert": "Expert",
            "advanced": "Advanced",
            "intermediate": "Proficient",
            "beginner": "Familiar",
            "general": None,
        }

        skill_cell_s = S("SkillCell", fontSize=9, fontName="Helvetica", textColor=MID, leading=13)
        label_cell_s = S("LabelCell", fontSize=8.5, fontName="Helvetica-Bold", textColor=ACCENT, leading=13)

        for lvl in ordered_levels:
            names = by_level[lvl]
            label = level_labels.get(lvl, lvl.capitalize())

            # Lay out in a 3-column grid
            COLS = 3
            padded = names + [""] * (-len(names) % COLS)   # pad to multiple of 3
            rows_data = [padded[i:i + COLS] for i in range(0, len(padded), COLS)]
            cell_rows = [
                [Paragraph(cell, skill_cell_s) for cell in row]
                for row in rows_data
            ]

            usable_w = 174 * mm   # A4 minus margins
            col_w = usable_w / COLS

            if label:
                story.append(Paragraph(label, label_cell_s))

            tbl = Table(cell_rows, colWidths=[col_w] * COLS, hAlign="LEFT")
            tbl.setStyle(TableStyle([
                ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING",   (0, 0), (-1, -1), 0),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
                ("TOPPADDING",    (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]))
            story.append(tbl)
            story.append(Spacer(1, 3))

        story.append(hr())

    # ── experience ──────────────────────────────────────────────────────
    if data.experiences:
        story.append(Paragraph("Work Experience", sec_style))
        for exp in data.experiences:
            role    = exp.get("role", "")
            company = exp.get("company", "")
            start   = _fmt_date(exp.get("start_date"))
            end     = _fmt_date(exp.get("end_date"))
            period  = f"{start} – {end}"

            # Two-column row: title+company left, dates right
            left_cell  = [Paragraph(role, job_title_s), Paragraph(company, company_s)]
            right_cell = [Paragraph(period, date_s)]
            tbl = Table([[left_cell, right_cell]], colWidths=["75%", "25%"])
            tbl.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING",  (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING",   (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
            ]))
            story.append(tbl)
            for b in _bullets(exp.get("description"), 6):
                story.append(Paragraph(b, bullet_s))
            story.append(Spacer(1, 4))
        story.append(hr())

    # ── projects ────────────────────────────────────────────────────────
    if data.projects:
        story.append(Paragraph("Projects", sec_style))
        for proj in data.projects:
            name  = proj.get("name", "")
            tech  = proj.get("technologies", "")
            desc  = proj.get("description", "")
            title = f"<b>{name}</b>" + (f"  <font color='#1e40af'>| {tech}</font>" if tech else "")
            story.append(Paragraph(title, normal_s))
            if desc:
                story.append(Paragraph(desc[:300], normal_s))
            story.append(Spacer(1, 3))
        story.append(hr())

    # ── education ───────────────────────────────────────────────────────
    if data.educations:
        story.append(Paragraph("Education", sec_style))
        for edu in data.educations:
            institution = edu.get("institution", "")
            degree      = edu.get("degree", "")
            field       = edu.get("field_of_study", "")
            start       = _fmt_date(edu.get("start_date"))
            end         = _fmt_date(edu.get("end_date"))
            degree_line = ", ".join(filter(None, [degree, field]))
            left_cell  = [Paragraph(f"<b>{institution}</b>", normal_s)]
            if degree_line:
                left_cell.append(Paragraph(degree_line, normal_s))
            right_cell = [Paragraph(f"{start} – {end}", date_s)]
            tbl = Table([[left_cell, right_cell]], colWidths=["75%", "25%"])
            tbl.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING",  (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING",   (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
            ]))
            story.append(tbl)
        story.append(hr())

    # ── certifications ──────────────────────────────────────────────────
    if data.certifications:
        story.append(Paragraph("Certifications", sec_style))
        for cert in data.certifications:
            name   = cert.get("name", "")
            issuer = cert.get("issuer", "")
            issued = _fmt_date(cert.get("issue_date"))
            parts  = [f"<b>{name}</b>"]
            if issuer:
                parts.append(issuer)
            if cert.get("issue_date"):
                parts.append(issued)
            story.append(Paragraph("  —  ".join(parts), normal_s))

    doc.build(story)
    return buf.getvalue()


# ── DOCX export ────────────────────────────────────────────────────────────

def _build_docx(data: _ResumeData) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor
    from docx.oxml import OxmlElement

    ACCENT_RGB = RGBColor(0x1e, 0x40, 0xAF)   # blue-800
    DARK_RGB   = RGBColor(0x11, 0x18, 0x27)    # gray-900
    MID_RGB    = RGBColor(0x37, 0x41, 0x51)    # gray-700
    LIGHT_RGB  = RGBColor(0x6b, 0x72, 0x80)    # gray-500

    doc = Document()

    # ── page margins ────────────────────────────────────────────────────
    for section in doc.sections:
        section.top_margin    = Inches(0.65)
        section.bottom_margin = Inches(0.65)
        section.left_margin   = Inches(0.75)
        section.right_margin  = Inches(0.75)

    def add_run(para, text: str, bold=False, italic=False,
                color: RGBColor | None = None, size_pt: float = 10):
        run = para.add_run(text)
        run.bold   = bold
        run.italic = italic
        if color:
            run.font.color.rgb = color
        run.font.size = Pt(size_pt)
        return run

    def section_heading(title: str):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after  = Pt(2)
        run = p.add_run(title.upper())
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = ACCENT_RGB
        # bottom border
        pPr = p._p.get_or_add_pPr()
        pBdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "4")
        bottom.set(qn("w:space"), "1")
        bottom.set(qn("w:color"), "1e40af")
        pBdr.append(bottom)
        pPr.append(pBdr)
        return p

    # ── name + headline ─────────────────────────────────────────────────
    name_p = doc.add_paragraph()
    name_p.paragraph_format.space_after = Pt(1)
    add_run(name_p, data.full_name or "Resume", bold=True, color=DARK_RGB, size_pt=22)

    if data.headline:
        h_p = doc.add_paragraph()
        h_p.paragraph_format.space_after = Pt(2)
        add_run(h_p, data.headline, color=ACCENT_RGB, size_pt=11)

    contact_parts = [p for p in [data.email, data.location] if p]
    if contact_parts:
        c_p = doc.add_paragraph()
        c_p.paragraph_format.space_after = Pt(6)
        add_run(c_p, "  ·  ".join(contact_parts), color=LIGHT_RGB, size_pt=9)

    # ── summary ─────────────────────────────────────────────────────────
    if data.summary:
        section_heading("Professional Summary")
        p = doc.add_paragraph(data.summary)
        p.paragraph_format.space_after = Pt(2)
        for run in p.runs:
            run.font.size = Pt(9.5)
            run.font.color.rgb = MID_RGB

    # ── skills ──────────────────────────────────────────────────────────
    if data.skills:
        section_heading("Skills")
        skill_names = [
            f"{s['name']} ({s['level']})" if s.get("level") else s["name"]
            for s in data.skills
        ]
        p = doc.add_paragraph("  ·  ".join(skill_names))
        p.paragraph_format.space_after = Pt(2)
        for run in p.runs:
            run.font.size = Pt(9.5)
            run.font.color.rgb = MID_RGB

    # ── experience ──────────────────────────────────────────────────────
    if data.experiences:
        section_heading("Work Experience")
        for exp in data.experiences:
            role    = exp.get("role", "")
            company = exp.get("company", "")
            start   = _fmt_date(exp.get("start_date"))
            end     = _fmt_date(exp.get("end_date"))

            row_p = doc.add_paragraph()
            row_p.paragraph_format.space_after = Pt(0)
            add_run(row_p, role, bold=True, color=DARK_RGB, size_pt=10.5)
            add_run(row_p, "  —  ", color=MID_RGB, size_pt=10)
            add_run(row_p, company, italic=True, color=ACCENT_RGB, size_pt=10)
            add_run(row_p, f"   {start} – {end}", color=LIGHT_RGB, size_pt=9)

            for b in _bullets(exp.get("description"), 6):
                bp = doc.add_paragraph(style="List Bullet")
                bp.paragraph_format.left_indent  = Inches(0.2)
                bp.paragraph_format.space_after  = Pt(1)
                run = bp.add_run(b)
                run.font.size = Pt(9.5)
                run.font.color.rgb = MID_RGB

            doc.add_paragraph().paragraph_format.space_after = Pt(3)

    # ── projects ────────────────────────────────────────────────────────
    if data.projects:
        section_heading("Projects")
        for proj in data.projects:
            name = proj.get("name", "")
            tech = proj.get("technologies", "")
            desc = proj.get("description", "")
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(1)
            add_run(p, name, bold=True, color=DARK_RGB, size_pt=10)
            if tech:
                add_run(p, f"  |  {tech}", color=ACCENT_RGB, size_pt=9)
            if desc:
                dp = doc.add_paragraph(desc[:300])
                dp.paragraph_format.space_after = Pt(2)
                for run in dp.runs:
                    run.font.size = Pt(9.5)
                    run.font.color.rgb = MID_RGB

    # ── education ───────────────────────────────────────────────────────
    if data.educations:
        section_heading("Education")
        for edu in data.educations:
            institution = edu.get("institution", "")
            degree      = edu.get("degree", "")
            field       = edu.get("field_of_study", "")
            start       = _fmt_date(edu.get("start_date"))
            end         = _fmt_date(edu.get("end_date"))
            degree_line = ", ".join(filter(None, [degree, field]))

            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(1)
            add_run(p, institution, bold=True, color=DARK_RGB, size_pt=10.5)
            add_run(p, f"   {start} – {end}", color=LIGHT_RGB, size_pt=9)
            if degree_line:
                dp = doc.add_paragraph(degree_line)
                dp.paragraph_format.space_after = Pt(3)
                for run in dp.runs:
                    run.font.size = Pt(9.5)
                    run.font.color.rgb = MID_RGB

    # ── certifications ───────────────────────────────────────────────────
    if data.certifications:
        section_heading("Certifications")
        for cert in data.certifications:
            name   = cert.get("name", "")
            issuer = cert.get("issuer", "")
            issued = _fmt_date(cert.get("issue_date"))
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(2)
            add_run(p, name, bold=True, color=DARK_RGB, size_pt=10)
            if issuer:
                add_run(p, f"  —  {issuer}", color=MID_RGB, size_pt=9.5)
            if cert.get("issue_date"):
                add_run(p, f"  ({issued})", color=LIGHT_RGB, size_pt=9)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── Public API ─────────────────────────────────────────────────────────────

class ResumeExporter:
    def __init__(self, db: Session, user: User):
        self.db   = db
        self.user = user

    def _load(self) -> _ResumeData:
        return _ResumeData(self.db, self.user.id, self.user.email)

    def as_pdf(self) -> bytes:
        try:
            return _build_pdf(self._load())
        except Exception as exc:
            logger.error("resume_pdf_error", error=str(exc))
            raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")

    def as_docx(self) -> bytes:
        try:
            return _build_docx(self._load())
        except Exception as exc:
            logger.error("resume_docx_error", error=str(exc))
            raise HTTPException(status_code=500, detail=f"DOCX generation failed: {exc}")

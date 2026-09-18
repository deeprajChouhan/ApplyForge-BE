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
    ApplicationCustomization,
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
    """Loads all profile sections for a user, merging DB + parsed fallback.

    When *app_id* is supplied the per-application AI customizations are also
    applied (skills_add, experiences_update, projects_add) so the export
    matches exactly what the user sees in the on-screen preview.
    """

    def __init__(self, db: Session, user_id: int, user_email: str, app_id: int | None = None):
        profile = db.query(UserProfile).filter_by(user_id=user_id).first()
        parsed_row = (
            db.query(ParsedResumeData)
            .filter_by(user_id=user_id)
            .filter(ParsedResumeData.deleted_at.is_(None))
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

        # Per-application AI customizations (applied suggestions)
        customizations: dict[str, Any] = {}
        if app_id is not None:
            cust_row = (
                db.query(ApplicationCustomization)
                .filter_by(user_id=user_id, application_id=app_id)
                .first()
            )
            if cust_row and cust_row.customizations_json:
                try:
                    raw = json.loads(cust_row.customizations_json)
                    customizations = raw if isinstance(raw, dict) else {}
                except (json.JSONDecodeError, TypeError):
                    customizations = {}

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
        # AI-suggested skills for this specific job application
        for s in customizations.get("skills_add", []):
            key = (s.get("name", "") or "").lower().strip()
            if key and key not in seen:
                seen.add(key)
                skills.append({"name": s["name"], "level": s.get("level")})
        self.skills = skills

        # Experiences
        db_exps = (
            db.query(WorkExperience)
            .filter_by(user_id=user_id)
            .order_by(WorkExperience.start_date.desc())
            .all()
        )
        exp_overrides: dict[str, dict] = customizations.get("experiences_update", {})
        if db_exps:
            self.experiences = [
                {
                    "role": e.role,
                    "company": e.company,
                    "start_date": e.start_date,
                    "end_date": e.end_date,
                    # Apply AI-suggested bullet overrides for this job if present
                    "description": (
                        exp_overrides.get(str(e.id), {}).get("description")
                        or e.description
                    ),
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

        # Education — union merge: DB rows + parsed-resume (deduped by institution+degree)
        db_edus = (
            db.query(Education)
            .filter_by(user_id=user_id)
            .order_by(Education.start_date.desc())
            .all()
        )
        seen_edu: set[str] = set()
        educations: list[dict] = []
        for e in db_edus:
            key = f"{(e.institution or '').lower().strip()}::{(e.degree or '').lower().strip()}"
            if key not in seen_edu:
                seen_edu.add(key)
                educations.append({
                    "institution": e.institution,
                    "degree": e.degree,
                    "field_of_study": e.field_of_study,
                    "start_date": e.start_date,
                    "end_date": e.end_date,
                })
        for e in parsed.get("education", []):
            key = f"{(e.get('institution', '') or '').lower().strip()}::{(e.get('degree', '') or '').lower().strip()}"
            if key not in seen_edu:
                seen_edu.add(key)
                educations.append({
                    "institution": e.get("institution", ""),
                    "degree": e.get("degree", ""),
                    "field_of_study": e.get("field_of_study", ""),
                    "start_date": e.get("start_date"),
                    "end_date": e.get("end_date"),
                })
        self.educations = educations

        # Projects — union merge: DB rows + parsed-resume + AI-suggested
        # (mirrors the frontend ATSResumeTemplate union-merge logic)
        db_projs = db.query(Project).filter_by(user_id=user_id).all()
        seen_proj: set[str] = set()
        projects: list[dict] = []
        for p in db_projs:
            key = (p.name or "").lower().strip()
            if key and key not in seen_proj:
                seen_proj.add(key)
                projects.append({
                    "name": p.name,
                    "description": p.description,
                    "technologies": p.technologies,
                })
        for p in parsed.get("projects", []):
            key = (p.get("name", "") or "").lower().strip()
            if key and key not in seen_proj:
                seen_proj.add(key)
                projects.append({
                    "name": p.get("name", ""),
                    "description": p.get("description", ""),
                    "technologies": p.get("technologies", ""),
                })
        # Append AI-suggested projects (gap-fix or job-specific extras)
        for p in customizations.get("projects_add", []):
            key = (p.get("name", "") or "").lower().strip()
            if key and key not in seen_proj:
                seen_proj.add(key)
                projects.append({
                    "name": p.get("name", ""),
                    "description": p.get("description", ""),
                    "technologies": p.get("technologies", ""),
                })
        self.projects = projects

        # Certifications — union merge: DB rows + parsed-resume
        db_certs = db.query(Certification).filter_by(user_id=user_id).all()
        seen_cert: set[str] = set()
        certifications: list[dict] = []
        for c in db_certs:
            key = (c.name or "").lower().strip()
            if key and key not in seen_cert:
                seen_cert.add(key)
                certifications.append({"name": c.name, "issuer": c.issuer, "issue_date": c.issue_date})
        for c in parsed.get("certifications", []):
            key = (c.get("name", "") or "").lower().strip()
            if key and key not in seen_cert:
                seen_cert.add(key)
                certifications.append({
                    "name": c.get("name", ""),
                    "issuer": c.get("issuer", ""),
                    "issue_date": c.get("issue_date"),
                })
        self.certifications = certifications


# ── Template-driven renderers ──────────────────────────────────────────────
#
# Both the PDF and DOCX renderers consume the SAME ResolvedTemplate
# (from ResumeTemplateResolver). The template's config_json decides:
#   - which sections appear and in what order (config.sections)
#   - typography (config.styles.font_family, body/heading sizes, line_height)
#   - margins (config.styles.margin_*)
#   - accent color and header alignment
#   - layout family (single_column | two_column | centered)
#
# The old hardcoded "Classic ATS" defaults are gone — the classic look is
# just the config a ResumeTemplate seeded with base_template='classic' carries.

from app.services.templates.resolver import ResolvedTemplate


# Section-key → renderer callable
def _section_renderer_map_pdf(data, styles, template, story_add, S):
    return {
        "header":         lambda: _pdf_header(data, styles, template, story_add, S),
        "summary":        lambda: _pdf_summary(data, styles, template, story_add, S),
        "skills":         lambda: _pdf_skills(data, styles, template, story_add, S),
        "experience":     lambda: _pdf_experience(data, styles, template, story_add, S),
        "projects":       lambda: _pdf_projects(data, styles, template, story_add, S),
        "education":      lambda: _pdf_education(data, styles, template, story_add, S),
        "certifications": lambda: _pdf_certifications(data, styles, template, story_add, S),
    }


def _pdf_font_family(styles: dict) -> tuple[str, str, str]:
    """Return (regular, bold, italic) reportlab font names for a config family."""
    fam = (styles.get("font_family") or "").strip().lower()
    if fam in ("times", "times new roman", "georgia", "serif"):
        return "Times-Roman", "Times-Bold", "Times-Italic"
    if fam in ("courier", "mono", "monospace"):
        return "Courier", "Courier-Bold", "Courier-Oblique"
    # Inter and any other sans → Helvetica (ReportLab core font)
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


def _section_label(template: ResolvedTemplate, key: str, fallback: str) -> str:
    for s in template.config.get("sections", []):
        if s.get("key") == key:
            lbl = s.get("label")
            if isinstance(lbl, str) and lbl.strip():
                return lbl
    return fallback


def _enabled_sections_in_order(template: ResolvedTemplate) -> list[str]:
    secs = [s for s in template.config.get("sections", []) if s.get("enabled")]
    secs.sort(key=lambda s: s.get("order", 999))
    return [s["key"] for s in secs]


# ── PDF ────────────────────────────────────────────────────────────────────

def _build_pdf(data: "_ResumeData", template: ResolvedTemplate) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_JUSTIFY, TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import (
        HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    styles = template.config.get("styles", {})
    regular, bold, italic = _pdf_font_family(styles)
    accent = colors.HexColor(styles.get("accent_color") or "#1D4ED8")
    dark   = colors.HexColor("#0F172A")
    body_c = colors.HexColor("#1E293B")
    mid    = colors.HexColor("#475569")
    light  = colors.HexColor("#64748B")

    body_size    = float(styles.get("body_font_size", 10))
    head_size    = float(styles.get("heading_font_size", 12))
    lh_ratio     = float(styles.get("line_height", 1.35))
    sec_spacing  = float(styles.get("section_spacing", 8))
    m_t          = float(styles.get("margin_top", 40))
    m_b          = float(styles.get("margin_bottom", 40))
    m_l          = float(styles.get("margin_left", 46))
    m_r          = float(styles.get("margin_right", 46))
    header_align = styles.get("header_style", "left")

    PAGE_W, PAGE_H = letter
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=m_l, rightMargin=m_r,
        topMargin=m_t, bottomMargin=m_b,
    )

    _base = getSampleStyleSheet()["Normal"]

    def S(name, **kw):
        return ParagraphStyle(name, parent=_base, **kw)

    style_pack = {
        "name":     S("N", fontName=bold, fontSize=head_size * 1.8, leading=head_size * 2.2,
                       textColor=dark, alignment=TA_CENTER if header_align == "centered" else TA_LEFT),
        "headline": S("H", fontName=regular, fontSize=body_size + 1, leading=(body_size + 1) * lh_ratio,
                       textColor=accent, alignment=TA_CENTER if header_align == "centered" else TA_LEFT),
        "contact":  S("C", fontName=regular, fontSize=body_size - 1, leading=(body_size - 1) * lh_ratio,
                       textColor=light, alignment=TA_CENTER if header_align == "centered" else TA_LEFT),
        "sec":      S("Sec", fontName=bold, fontSize=head_size, leading=head_size * 1.2,
                       textColor=accent, spaceBefore=sec_spacing, spaceAfter=2),
        "body":     S("B", fontName=regular, fontSize=body_size, leading=body_size * lh_ratio,
                       textColor=body_c, alignment=TA_JUSTIFY),
        "bullet":   S("Bul", fontName=regular, fontSize=body_size, leading=body_size * lh_ratio,
                       textColor=body_c, leftIndent=14, spaceAfter=1.5),
        "role":     S("R", fontName=bold, fontSize=body_size + 0.5, leading=(body_size + 0.5) * lh_ratio,
                       textColor=dark),
        "company":  S("Co", fontName=italic, fontSize=body_size, leading=body_size * lh_ratio,
                       textColor=accent),
        "date":     S("D", fontName=regular, fontSize=body_size - 1, leading=(body_size - 1) * lh_ratio,
                       textColor=mid, alignment=TA_RIGHT),
        "sub":      S("Sub", fontName=italic, fontSize=body_size, leading=body_size * lh_ratio,
                       textColor=mid),
        "accent":   accent,
        "rule":     colors.HexColor("#CBD5E1"),
    }

    story: list = []
    add = story.append

    def rule(color=None, thickness=1.0):
        add(HRFlowable(width="100%", thickness=thickness,
                       color=color or style_pack["accent"], spaceAfter=4))

    def section_head(label: str):
        add(Paragraph(label.upper(), style_pack["sec"]))
        rule()

    renderers = {
        "header":         lambda: _pdf_header(data, style_pack, template, add, rule),
        "summary":        lambda: _pdf_simple(data.summary, "summary", template, style_pack, add, section_head),
        "skills":         lambda: _pdf_skills(data, style_pack, template, add, section_head),
        "experience":     lambda: _pdf_experience(data, style_pack, template, add, section_head),
        "projects":       lambda: _pdf_projects(data, style_pack, template, add, section_head),
        "education":      lambda: _pdf_education(data, style_pack, template, add, section_head),
        "certifications": lambda: _pdf_certifications(data, style_pack, template, add, section_head),
    }

    for key in _enabled_sections_in_order(template):
        fn = renderers.get(key)
        if fn:
            fn()

    doc.build(story)
    return buf.getvalue()


def _pdf_header(data, sp, template, add, rule):
    from reportlab.platypus import Paragraph, Spacer
    add(Paragraph(data.full_name or "Resume", sp["name"]))
    if data.headline:
        add(Paragraph(data.headline, sp["headline"]))
    contact = "  ·  ".join([p for p in [data.email, data.location] if p])
    if contact:
        add(Paragraph(contact, sp["contact"]))
    add(Spacer(1, 4))


def _pdf_simple(text, key, template, sp, add, section_head):
    from reportlab.platypus import Paragraph
    if not text:
        return
    section_head(_section_label(template, key, "Professional Summary"))
    add(Paragraph(text, sp["body"]))


def _pdf_skills(data, sp, template, add, section_head):
    from reportlab.platypus import Paragraph
    if not data.skills:
        return
    section_head(_section_label(template, "skills", "Skills"))
    names = [f"{s['name']} ({s['level']})" if s.get("level") else s["name"] for s in data.skills]
    add(Paragraph("  ·  ".join(names), sp["body"]))


def _pdf_experience(data, sp, template, add, section_head):
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    if not data.experiences:
        return
    section_head(_section_label(template, "experience", "Work Experience"))
    for e in data.experiences:
        role = e.get("role", "")
        company = e.get("company", "")
        start = _fmt_date(e.get("start_date"))
        end = _fmt_date(e.get("end_date"))
        left = Paragraph(f"<b>{role}</b>  —  <i>{company}</i>", sp["role"])
        right = Paragraph(f"{start} – {end}", sp["date"])
        t = Table([[left, right]], colWidths=["70%", "30%"])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        add(t)
        for b in _bullets(e.get("description"), 6):
            add(Paragraph(f"• {b}", sp["bullet"]))
        add(Spacer(1, 3))


def _pdf_projects(data, sp, template, add, section_head):
    from reportlab.platypus import Paragraph, Spacer
    if not data.projects:
        return
    section_head(_section_label(template, "projects", "Projects"))
    for p in data.projects:
        name = p.get("name", "")
        tech = p.get("technologies", "")
        desc = p.get("description", "")
        line = f"<b>{name}</b>"
        if tech:
            line += f"  |  <font color='{sp['accent'].hexval()}'>{tech}</font>"
        add(Paragraph(line, sp["role"]))
        if desc:
            add(Paragraph(desc[:400], sp["sub"]))
        add(Spacer(1, 2))


def _pdf_education(data, sp, template, add, section_head):
    from reportlab.platypus import Paragraph, Table, TableStyle
    if not data.educations:
        return
    section_head(_section_label(template, "education", "Education"))
    for edu in data.educations:
        inst = edu.get("institution", "")
        degree = edu.get("degree", "")
        field = edu.get("field_of_study", "")
        start = _fmt_date(edu.get("start_date"))
        end = _fmt_date(edu.get("end_date"))
        line = ", ".join(filter(None, [degree, field]))
        left = Paragraph(f"<b>{inst}</b>", sp["role"])
        right = Paragraph(f"{start} – {end}", sp["date"])
        t = Table([[left, right]], colWidths=["70%", "30%"])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        add(t)
        if line:
            add(Paragraph(line, sp["sub"]))


def _pdf_certifications(data, sp, template, add, section_head):
    from reportlab.platypus import Paragraph
    if not data.certifications:
        return
    section_head(_section_label(template, "certifications", "Certifications"))
    for c in data.certifications:
        name = c.get("name", "")
        issuer = c.get("issuer", "")
        issued = _fmt_date(c.get("issue_date"))
        parts = [f"<b>{name}</b>"]
        if issuer:
            parts.append(f"— {issuer}")
        if c.get("issue_date"):
            parts.append(f"({issued})")
        add(Paragraph(" ".join(parts), sp["body"]))


# ── DOCX ───────────────────────────────────────────────────────────────────

def _build_docx(data: "_ResumeData", template: ResolvedTemplate) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    styles = template.config.get("styles", {})
    body_size = float(styles.get("body_font_size", 10))
    head_size = float(styles.get("heading_font_size", 12))
    accent_hex = (styles.get("accent_color") or "#1D4ED8").lstrip("#")
    header_align = styles.get("header_style", "left")
    font_name = styles.get("font_family") or "Helvetica"
    # python-docx is fine with any font string; Word will fall back locally.

    ACCENT = RGBColor.from_string(accent_hex)
    DARK   = RGBColor(0x0F, 0x17, 0x2A)
    MID    = RGBColor(0x47, 0x55, 0x69)
    LIGHT  = RGBColor(0x64, 0x74, 0x8B)

    doc = Document()
    section = doc.sections[0]
    section.top_margin    = Inches(float(styles.get("margin_top",    40)) / 72)
    section.bottom_margin = Inches(float(styles.get("margin_bottom", 40)) / 72)
    section.left_margin   = Inches(float(styles.get("margin_left",   46)) / 72)
    section.right_margin  = Inches(float(styles.get("margin_right",  46)) / 72)

    def add_run(p, text, *, bold=False, italic=False, color=None, size_pt=None):
        r = p.add_run(text)
        r.font.name = font_name
        r.bold = bold
        r.italic = italic
        if color is not None:
            r.font.color.rgb = color
        if size_pt is not None:
            r.font.size = Pt(size_pt)
        return r

    def section_heading(label):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after = Pt(2)
        add_run(p, label.upper(), bold=True, color=ACCENT, size_pt=head_size)
        # thin bottom border
        pPr = p._p.get_or_add_pPr()
        pBdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "4")
        bottom.set(qn("w:space"), "1")
        bottom.set(qn("w:color"), accent_hex)
        pBdr.append(bottom)
        pPr.append(pBdr)
        return p

    def render_header():
        p = doc.add_paragraph()
        if header_align == "centered":
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        add_run(p, data.full_name or "Resume", bold=True, color=DARK, size_pt=head_size * 1.8)
        if data.headline:
            hp = doc.add_paragraph()
            if header_align == "centered":
                hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            add_run(hp, data.headline, color=ACCENT, size_pt=body_size + 1)
        contact_parts = [p for p in [data.email, data.location] if p]
        if contact_parts:
            cp = doc.add_paragraph()
            if header_align == "centered":
                cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            add_run(cp, "  ·  ".join(contact_parts), color=LIGHT, size_pt=body_size - 1)

    def render_summary():
        if not data.summary:
            return
        section_heading(_section_label(template, "summary", "Professional Summary"))
        p = doc.add_paragraph(data.summary)
        for r in p.runs:
            r.font.size = Pt(body_size)
            r.font.color.rgb = MID
            r.font.name = font_name

    def render_skills():
        if not data.skills:
            return
        section_heading(_section_label(template, "skills", "Skills"))
        names = [f"{s['name']} ({s['level']})" if s.get("level") else s["name"] for s in data.skills]
        p = doc.add_paragraph("  ·  ".join(names))
        for r in p.runs:
            r.font.size = Pt(body_size)
            r.font.color.rgb = MID
            r.font.name = font_name

    def render_experience():
        if not data.experiences:
            return
        section_heading(_section_label(template, "experience", "Work Experience"))
        for e in data.experiences:
            role = e.get("role", "")
            company = e.get("company", "")
            start = _fmt_date(e.get("start_date"))
            end = _fmt_date(e.get("end_date"))
            row = doc.add_paragraph()
            add_run(row, role, bold=True, color=DARK, size_pt=body_size + 0.5)
            add_run(row, "  —  ", color=MID, size_pt=body_size)
            add_run(row, company, italic=True, color=ACCENT, size_pt=body_size)
            add_run(row, f"   {start} – {end}", color=LIGHT, size_pt=body_size - 1)
            for b in _bullets(e.get("description"), 6):
                bp = doc.add_paragraph(style="List Bullet")
                bp.paragraph_format.left_indent = Inches(0.2)
                r = bp.add_run(b)
                r.font.size = Pt(body_size)
                r.font.color.rgb = MID
                r.font.name = font_name

    def render_projects():
        if not data.projects:
            return
        section_heading(_section_label(template, "projects", "Projects"))
        for proj in data.projects:
            name = proj.get("name", "")
            tech = proj.get("technologies", "")
            desc = proj.get("description", "")
            p = doc.add_paragraph()
            add_run(p, name, bold=True, color=DARK, size_pt=body_size)
            if tech:
                add_run(p, f"  |  {tech}", color=ACCENT, size_pt=body_size - 1)
            if desc:
                dp = doc.add_paragraph(desc[:400])
                for r in dp.runs:
                    r.font.size = Pt(body_size)
                    r.font.color.rgb = MID
                    r.font.name = font_name

    def render_education():
        if not data.educations:
            return
        section_heading(_section_label(template, "education", "Education"))
        for edu in data.educations:
            inst = edu.get("institution", "")
            degree = edu.get("degree", "")
            field = edu.get("field_of_study", "")
            start = _fmt_date(edu.get("start_date"))
            end = _fmt_date(edu.get("end_date"))
            line = ", ".join(filter(None, [degree, field]))
            p = doc.add_paragraph()
            add_run(p, inst, bold=True, color=DARK, size_pt=body_size + 0.5)
            add_run(p, f"   {start} – {end}", color=LIGHT, size_pt=body_size - 1)
            if line:
                dp = doc.add_paragraph(line)
                for r in dp.runs:
                    r.font.size = Pt(body_size)
                    r.font.color.rgb = MID
                    r.font.name = font_name

    def render_certifications():
        if not data.certifications:
            return
        section_heading(_section_label(template, "certifications", "Certifications"))
        for c in data.certifications:
            name = c.get("name", "")
            issuer = c.get("issuer", "")
            issued = _fmt_date(c.get("issue_date"))
            p = doc.add_paragraph()
            add_run(p, name, bold=True, color=DARK, size_pt=body_size)
            if issuer:
                add_run(p, f"  —  {issuer}", color=MID, size_pt=body_size - 0.5)
            if c.get("issue_date"):
                add_run(p, f"  ({issued})", color=LIGHT, size_pt=body_size - 1)

    renderers = {
        "header":         render_header,
        "summary":        render_summary,
        "skills":         render_skills,
        "experience":     render_experience,
        "projects":       render_projects,
        "education":      render_education,
        "certifications": render_certifications,
    }
    for key in _enabled_sections_in_order(template):
        fn = renderers.get(key)
        if fn:
            fn()

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── Public API ─────────────────────────────────────────────────────────────

class ResumeExporter:
    """
    Export the user's resume as PDF or DOCX using a resolved template.

    Callers MUST pass a ResolvedTemplate (from ResumeTemplateResolver).
    The exporter no longer picks a default template internally — that was
    the exact source of the bug where switching templates in the UI still
    exported the classic layout.
    """
    def __init__(self, db: Session, user: User, template: ResolvedTemplate,
                 app_id: int | None = None):
        self.db       = db
        self.user     = user
        self.template = template
        self.app_id   = app_id

    def _load(self) -> "_ResumeData":
        return _ResumeData(self.db, self.user.id, self.user.email, app_id=self.app_id)

    def as_pdf(self) -> bytes:
        try:
            return _build_pdf(self._load(), self.template)
        except Exception as exc:
            logger.error("resume_pdf_error", error=str(exc))
            raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")

    def as_docx(self) -> bytes:
        try:
            return _build_docx(self._load(), self.template)
        except Exception as exc:
            logger.error("resume_docx_error", error=str(exc))
            raise HTTPException(status_code=500, detail=f"DOCX generation failed: {exc}")

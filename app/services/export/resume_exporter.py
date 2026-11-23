"""
Resume Export Service
=====================
Generates downloadable PDF (reportlab) and DOCX (python-docx) resumes.

Two guarantees:

1. The download matches the on-screen preview.
   * Data: `_ResumeData` uses the same sources and precedence as the
     frontend `useResumeData` hook (user-frontend/.../resume-templates/
     shared-data.ts): the resume pinned to the application (else the newest
     parse) wins, DB profile tables fill gaps, and the per-application AI
     customizations are layered on top.
   * Design: each template family (classic / modern / sidebar / executive)
     has its own `_Design` preset mirroring its preview component. The
     resolved template's config can still rename/reorder/hide sections and
     override styles.

2. Every design is ATS-safe: one column, real selectable text in reading
   order, standard section headings, no images, no text boxes. The only
   table used in the PDF is a single-row title/date line (and the Modern
   header's one-cell accent bar), both of which extract in order.
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any
from xml.sax.saxutils import escape as _xml_escape

import structlog
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import (
    ApplicationCustomization,
    ApplicationTailoring,
    Certification,
    Education,
    JobApplication,
    ParsedResumeData,
    Project,
    Skill,
    User,
    UserProfile,
    WorkExperience,
)
from app.services.templates.defaults import (
    ALLOWED_SECTION_KEYS,
    DEFAULT_SECTIONS,
    DEFAULT_STYLES,
)
from app.services.templates.resolver import ResolvedTemplate

logger = structlog.get_logger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _parse_date(d: Any) -> date | None:
    if d is None or d == "":
        return None
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    s = str(d).strip()
    for candidate in (s, s[:10]):
        for fmt in ("%Y-%m-%d", "%Y-%m", "%Y/%m", "%Y", "%b %Y", "%B %Y", "%m/%Y"):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def _fmt_date(d: Any) -> str:
    """Mirror of the frontend fmtMonthYear: empty → 'Present', unparseable → as-is."""
    if d is None or d == "":
        return "Present"
    if isinstance(d, str) and d.strip().lower() in {"present", "current", "now"}:
        return "Present"
    parsed = _parse_date(d)
    if parsed is None:
        return str(d)
    return f"{_MONTHS[parsed.month - 1]} {parsed.year}"


def _fmt_range(start: Any, end: Any) -> str:
    s = _fmt_date(start) if start not in (None, "") else ""
    e = _fmt_date(end)
    if not s:
        return e
    return f"{s} – {e}"


def _sort_key(item: dict) -> date:
    return _parse_date(item.get("start_date")) or date.min


_BULLET_PREFIX = re.compile(r"^[•*\-–]\s*")


def _bullets(description: str | None, max_bullets: int = 5) -> list[str]:
    """Mirror of the frontend parseBullets()."""
    if not description:
        return []
    lines = [_BULLET_PREFIX.sub("", l.strip()) for l in description.split("\n")]
    lines = [l for l in lines if l]
    if len(lines) > 1:
        return lines[:max_bullets]
    parts = [p.strip() for p in re.split(r"(?<=\.\s)", description) if p.strip()]
    return parts[:max_bullets]


def _truncate_summary(summary: str, max_chars: int) -> str:
    """Mirror of the frontend truncateSummary()."""
    if not summary:
        return ""
    cleaned = re.sub(r"\s+", " ", summary).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    cut = cleaned[:max_chars]
    last_stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if last_stop > max_chars * 0.6:
        return cut[: last_stop + 1].strip()
    return (cut.strip() + "…").strip()


def _first(*vals: Any) -> Any:
    for v in vals:
        if v not in (None, ""):
            return v
    return None


def _prefer(primary: Any, fallback: Any) -> list:
    if isinstance(primary, list) and primary:
        return primary
    if isinstance(fallback, list) and fallback:
        return fallback
    return []


def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).lower().strip()


def _x(text: Any) -> str:
    """Escape user text for reportlab Paragraph mini-markup."""
    return _xml_escape(str(text or ""))


# ── data loader ────────────────────────────────────────────────────────────

class _ResumeData:
    """All resume content for one export, merged exactly like the preview."""

    def __init__(self, db: Session, user_id: int, user_email: str, app_id: int | None = None):
        profile = db.query(UserProfile).filter_by(user_id=user_id).first()

        app: JobApplication | None = None
        if app_id is not None:
            app = db.get(JobApplication, app_id)
            if app is not None and app.user_id != user_id:
                app = None

        # Pinned parse for this application, else the newest one.
        parsed_row = None
        if app is not None and app.selected_resume_id:
            parsed_row = (
                db.query(ParsedResumeData)
                .filter_by(id=app.selected_resume_id, user_id=user_id)
                .filter(ParsedResumeData.deleted_at.is_(None))
                .first()
            )
        if parsed_row is None:
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

        # Identity — uploaded resume wins, DB profile is fallback.
        self.full_name: str = _first(parsed.get("full_name"), profile and profile.full_name) or ""
        self.headline: str = _first(parsed.get("headline"), profile and profile.headline) or ""
        self.summary: str = _first(parsed.get("summary"), profile and profile.summary) or ""
        self.location: str = _first(parsed.get("location"), profile and profile.location) or ""
        self.email: str = _first(parsed.get("email"), user_email) or ""
        self.phone: str = _first(parsed.get("phone"), profile and profile.phone_number) or ""
        self.linkedin: str = _first(parsed.get("linkedin_url"), parsed.get("linkedin")) or ""
        self.website: str = _first(parsed.get("website_url"), parsed.get("website")) or ""

        # Per-application AI customizations (applied suggestions)
        customizations: dict[str, Any] = {}
        if app is not None:
            cust_row = (
                db.query(ApplicationCustomization)
                .filter_by(user_id=user_id, application_id=app.id)
                .first()
            )
            if cust_row and cust_row.customizations_json:
                try:
                    raw = json.loads(cust_row.customizations_json)
                    customizations = raw if isinstance(raw, dict) else {}
                except (json.JSONDecodeError, TypeError):
                    customizations = {}

        # JD keywords (same list the page passes to the preview)
        self.jd_keywords: list[str] = []
        if app is not None and app.jd_analysis_json:
            try:
                jd = app.jd_analysis_json
                jd = json.loads(jd) if isinstance(jd, str) else jd
                if isinstance(jd, dict):
                    self.jd_keywords = [
                        str(k).lower().strip()
                        for k in (jd.get("keywords") or []) + (jd.get("required_skills") or [])
                        if str(k).strip()
                    ]
            except (json.JSONDecodeError, TypeError):
                pass

        # Experiences — parsed wins; AI rewrites matched by id or role+company.
        db_exps = [
            {"id": e.id, "role": e.role, "company": e.company, "location": getattr(e, "location", None),
             "start_date": e.start_date, "end_date": e.end_date, "description": e.description}
            for e in db.query(WorkExperience).filter_by(user_id=user_id).all()
        ]
        overrides: dict[str, dict] = customizations.get("experiences_update") or {}
        by_role_company = {
            f"{_norm(o.get('role'))}::{_norm(o.get('company'))}": o
            for o in overrides.values()
            if isinstance(o, dict) and (o.get("role") or o.get("company"))
        }
        exps = []
        for e in _prefer(parsed.get("work_experience"), db_exps):
            if not isinstance(e, dict):
                continue
            ov = (overrides.get(str(e["id"])) if e.get("id") is not None else None) \
                or by_role_company.get(f"{_norm(e.get('role'))}::{_norm(e.get('company'))}")
            if ov and ov.get("description"):
                e = {**e, "description": ov["description"]}
            exps.append(e)
        self.experiences = sorted(exps, key=_sort_key, reverse=True)

        # Education — parsed wins; dedup by institution+degree; newest first.
        db_edus = [
            {"institution": e.institution, "degree": e.degree, "field_of_study": e.field_of_study,
             "start_date": e.start_date, "end_date": e.end_date}
            for e in db.query(Education).filter_by(user_id=user_id).all()
        ]
        seen: set[str] = set()
        edus = []
        for e in _prefer(parsed.get("education"), db_edus):
            if not isinstance(e, dict):
                continue
            k = f"{_norm(e.get('institution'))}::{_norm(e.get('degree'))}"
            if k not in seen:
                seen.add(k)
                edus.append(e)
        self.educations = sorted(edus, key=_sort_key, reverse=True)

        # Skills — parsed wins, then accepted AI skills; JD matches first.
        db_skills = [{"name": s.name, "level": s.level}
                     for s in db.query(Skill).filter_by(user_id=user_id).all()]
        seen = set()
        skills: list[dict] = []
        for s in list(_prefer(parsed.get("skills"), db_skills)) + list(customizations.get("skills_add") or []):
            item = {"name": s} if isinstance(s, str) else (s if isinstance(s, dict) else {})
            k = _norm(item.get("name"))
            if k and k not in seen:
                seen.add(k)
                skills.append({"name": item.get("name"), "level": item.get("level")})
        hit = [s for s in skills if self.matches_jd(s["name"])]
        rest = [s for s in skills if not self.matches_jd(s["name"])]
        self.skills = hit + rest

        # Projects — parsed wins, then AI-added projects appended.
        db_projs = [{"name": p.name, "description": p.description, "technologies": p.technologies}
                    for p in db.query(Project).filter_by(user_id=user_id).all()]
        seen = set()
        projs = []
        for p in _prefer(parsed.get("projects"), db_projs):
            if not isinstance(p, dict):
                continue
            k = _norm(p.get("name"))
            if k and k not in seen:
                seen.add(k)
                projs.append(p)
        for p in customizations.get("projects_add") or []:
            if isinstance(p, dict):
                projs.append({"name": p.get("name"), "description": p.get("description"),
                              "technologies": p.get("technologies")})
        self.projects = projs

        # Certifications — parsed wins.
        db_certs = [{"name": c.name, "issuer": c.issuer, "issue_date": c.issue_date}
                    for c in db.query(Certification).filter_by(user_id=user_id).all()]
        seen = set()
        certs = []
        for c in _prefer(parsed.get("certifications"), db_certs):
            if not isinstance(c, dict):
                continue
            k = _norm(c.get("name"))
            if k and k not in seen:
                seen.add(k)
                certs.append(c)
        self.certifications = certs

        # Evidence-mapped ATS tailoring (application_tailorings) — applied last so
        # the export matches the preview exactly. Skipped when switched off.
        if app is not None:
            self._apply_tailoring(db, user_id, app.id)

    def _apply_tailoring(self, db: Session, user_id: int, app_id: int) -> None:
        row = (
            db.query(ApplicationTailoring)
            .filter_by(user_id=user_id, application_id=app_id)
            .first()
        )
        if row is None or not row.enabled:
            return
        try:
            t = json.loads(row.tailored_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(t, dict):
            return
        if t.get("headline"):
            self.headline = str(t["headline"])
        if t.get("summary"):
            self.summary = str(t["summary"])
        if isinstance(t.get("skills"), list) and t["skills"]:
            levels = {_norm(s.get("name")): s.get("level") for s in self.skills}
            self.skills = [{"name": str(n), "level": levels.get(_norm(n))} for n in t["skills"] if str(n).strip()]
        by_rc = {
            f"{_norm(e.get('role'))}::{_norm(e.get('company'))}": e
            for e in t.get("experiences") or [] if isinstance(e, dict)
        }
        self.experiences = [
            {**e, "description": by_rc[k]["description"]}
            if (k := f"{_norm(e.get('role'))}::{_norm(e.get('company'))}") in by_rc and by_rc[k].get("description")
            else e
            for e in self.experiences
        ]
        by_name = {_norm(p.get("name")): p for p in t.get("projects") or [] if isinstance(p, dict)}
        self.projects = [
            {**p, "description": by_name[_norm(p.get("name"))]["description"]}
            if _norm(p.get("name")) in by_name and by_name[_norm(p.get("name"))].get("description")
            else p
            for p in self.projects
        ]

    def matches_jd(self, name: Any) -> bool:
        """Mirror of the frontend isKeywordMatch()."""
        n = _norm(name)
        if not n:
            return False
        return any(k in n or (len(k) > 3 and n in k) for k in self.jd_keywords)

    def contact_parts(self) -> list[str]:
        return [p for p in (self.email, self.phone, self.location, self.linkedin, self.website) if p]


# ── designs ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Design:
    family: str
    serif: bool
    accent: str                    # headings / highlights
    ink: str = "#0F172A"           # primary text
    body: str = "#1E293B"
    muted: str = "#64748B"
    header_align: str = "left"     # left | centered
    name_upper: bool = False
    name_bold: bool = True
    name_size: float = 22
    headline_italic: bool = False
    contact_sep: str = "  ·  "
    header_band: bool = False      # Navy Header
    header_bar: bool = False       # Modern left accent bar
    header_rule: str | None = None # rule colour under the header
    section_align: str = "left"
    section_rule: str | None = None
    section_rule_width: float = 0.75
    role_company_sep: str = "  —  "
    company_italic: bool = True
    skills_centered: bool = False
    body_size: float = 10
    heading_size: float = 10.5
    line_height: float = 1.35
    section_spacing: float = 9
    margins: tuple[float, float, float, float] = (40, 40, 46, 46)  # t, b, l, r
    summary_max: int = 380
    projects_max: int | None = None
    skills_max: int | None = None
    labels: dict[str, str] = field(default_factory=dict)
    order: tuple[str, ...] = ("header", "summary", "skills", "experience",
                              "projects", "education", "certifications")


_DESIGNS: dict[str, _Design] = {
    # Centred serif, black rules — template-classic.tsx
    "classic": _Design(
        family="classic", serif=True, accent="#111111", ink="#111111", body="#111111",
        muted="#444444", header_align="centered", name_size=20, section_rule="#111111",
        contact_sep="  |  ", company_italic=True, projects_max=3,
        labels={"summary": "Summary", "skills": "Skills", "experience": "Experience",
                "projects": "Projects", "education": "Education", "certifications": "Certifications"},
    ),
    # Sans, teal accent, left accent bar — template-modern.tsx
    "modern": _Design(
        family="modern", serif=False, accent="#0F766E", header_bar=True,
        section_rule="#0F766E", section_rule_width=1.25, role_company_sep="  ·  ",
        company_italic=False, projects_max=3,
        labels={"summary": "Professional Summary", "skills": "Core Skills",
                "experience": "Experience", "projects": "Selected Projects",
                "education": "Education", "certifications": "Certifications"},
    ),
    # Navy band, single column — template-sidebar.tsx (Navy Header)
    "sidebar": _Design(
        family="sidebar", serif=False, accent="#1E3A5F", header_band=True,
        contact_sep="  |  ", section_rule="#1E3A5F", company_italic=False,
        summary_max=420, margins=(28, 40, 46, 46),
        labels={"summary": "Summary", "skills": "Skills", "experience": "Experience",
                "projects": "Projects", "education": "Education", "certifications": "Certifications"},
    ),
    # Centred serif, uppercase name, centred headings — template-executive.tsx
    "executive": _Design(
        family="executive", serif=True, accent="#111111", ink="#111111", muted="#475569",
        header_align="centered", name_upper=True, name_bold=False, name_size=24,
        headline_italic=True, contact_sep="  |  ", header_rule="#94A3B8",
        section_align="center", section_rule="#94A3B8", section_rule_width=0.5,
        skills_centered=True, projects_max=2, skills_max=18, margins=(46, 46, 52, 52),
        labels={"summary": "Profile", "skills": "Areas of Expertise", "experience": "Experience",
                "projects": "Key Projects", "education": "Education",
                "certifications": "Certifications"},
        order=("header", "summary", "experience", "skills", "projects",
               "education", "certifications"),
    ),
}

_SERIF_HINTS = ("georgia", "times", "garamond", "serif", "cambria", "book")
_DEFAULT_LABELS = {s["key"]: s["label"] for s in DEFAULT_SECTIONS}
_DEFAULT_ORDER = [s["key"] for s in DEFAULT_SECTIONS]


def _design_for(template: ResolvedTemplate) -> tuple[_Design, list[str], dict[str, str]]:
    """
    Family preset + the template config's user edits.

    Returns (design, ordered enabled section keys, labels). A config value
    only overrides the preset when it differs from the generic default, so
    an untouched copy of a system template renders exactly like its preview.
    """
    base = _DESIGNS.get(template.base_template, _DESIGNS["classic"])
    cfg = template.config or {}
    st = cfg.get("styles") or {}

    def changed(key: str) -> bool:
        return key in st and st[key] != DEFAULT_STYLES.get(key)

    d = base
    if changed("font_family"):
        d = replace(d, serif=any(h in str(st["font_family"]).lower() for h in _SERIF_HINTS))
    if changed("accent_color"):
        d = replace(d, accent=st["accent_color"],
                    section_rule=st["accent_color"] if d.section_rule else None)
    if changed("header_style"):
        d = replace(d, header_align=st["header_style"])
    if changed("body_font_size"):
        d = replace(d, body_size=float(st["body_font_size"]))
    if changed("heading_font_size"):
        d = replace(d, heading_size=float(st["heading_font_size"]))
    if changed("line_height"):
        d = replace(d, line_height=float(st["line_height"]))
    if changed("section_spacing"):
        d = replace(d, section_spacing=float(st["section_spacing"]))
    t, b, l, r = d.margins
    d = replace(d, margins=(
        float(st["margin_top"]) if changed("margin_top") else t,
        float(st["margin_bottom"]) if changed("margin_bottom") else b,
        float(st["margin_left"]) if changed("margin_left") else l,
        float(st["margin_right"]) if changed("margin_right") else r,
    ))

    sections = [s for s in (cfg.get("sections") or []) if s.get("key") in ALLOWED_SECTION_KEYS]
    if not sections:
        sections = [dict(s) for s in DEFAULT_SECTIONS]
    enabled = {s["key"] for s in sections if s.get("enabled")}
    cfg_order = [s["key"] for s in sorted(sections, key=lambda s: s.get("order", 0))]
    order = list(d.order) if cfg_order == _DEFAULT_ORDER else cfg_order
    order = [k for k in order if k in enabled]
    if "header" in enabled and "header" in order:
        order.remove("header")
        order.insert(0, "header")  # the header always leads

    labels = dict(d.labels)
    for s in sections:
        lbl = (s.get("label") or "").strip()
        if lbl and lbl != _DEFAULT_LABELS.get(s["key"]):
            labels[s["key"]] = lbl
    return d, order, labels


# ── PDF ────────────────────────────────────────────────────────────────────

def _build_pdf(data: _ResumeData, template: ResolvedTemplate) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (
        HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    d, order, labels = _design_for(template)
    reg, bold, ital = (("Times-Roman", "Times-Bold", "Times-Italic") if d.serif
                       else ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique"))
    C = colors.HexColor
    accent, ink, body_c, muted = C(d.accent), C(d.ink), C(d.body), C(d.muted)
    bs, hs, lh = d.body_size, d.heading_size, d.line_height
    band = d.header_band
    hdr_color = colors.white if band else ink
    hdr_sub = C("#E2E8F0") if band else muted
    h_align = TA_CENTER if d.header_align == "centered" else TA_LEFT

    def S(name: str, **kw) -> ParagraphStyle:
        kw.setdefault("fontName", reg)
        kw.setdefault("fontSize", bs)
        kw.setdefault("leading", kw["fontSize"] * lh)
        kw.setdefault("textColor", body_c)
        return ParagraphStyle(name, **kw)

    st = {
        "name": S("name", fontName=bold if d.name_bold else reg, fontSize=d.name_size,
                  leading=d.name_size * 1.2, textColor=hdr_color, alignment=h_align),
        "headline": S("headline", fontName=ital if d.headline_italic else (reg if d.serif else bold),
                      fontSize=bs + 1, textColor=(hdr_sub if band else (accent if d.family == "modern" else muted)),
                      alignment=h_align, spaceBefore=2),
        "contact": S("contact", fontSize=bs - 0.5, textColor=hdr_sub, alignment=h_align, spaceBefore=3),
        "sec": S("sec", fontName=bold, fontSize=hs, leading=hs * 1.25, textColor=accent,
                 alignment=TA_CENTER if d.section_align == "center" else TA_LEFT,
                 spaceBefore=d.section_spacing, spaceAfter=1),
        "body": S("body"),
        "center": S("center", alignment=TA_CENTER),
        "bullet": S("bullet", leftIndent=12, bulletIndent=2, spaceAfter=1.2),
        "role": S("role", fontName=bold, fontSize=bs + 0.5, textColor=ink),
        "date": S("date", fontSize=bs - 1, textColor=muted, alignment=TA_RIGHT),
        "sub": S("sub", fontName=ital if d.serif else reg, textColor=muted),
    }
    company_tag = ("i",) if d.company_italic else ()
    company_color = d.accent if d.family in ("modern", "sidebar") else d.ink

    def company_markup(company: str) -> str:
        txt = f"<font color='{company_color}'>{_x(company)}</font>"
        for t in company_tag:
            txt = f"<{t}>{txt}</{t}>"
        return txt

    story: list = []
    add = story.append

    def title_row(left_markup: str, right_text: str, style=st["role"]):
        left = Paragraph(left_markup, style)
        if not right_text:
            add(left)
            return
        t = Table([[left, Paragraph(_x(right_text), st["date"])]], colWidths=["72%", "28%"])
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        add(t)

    def section_head(key: str):
        label = labels.get(key, _DEFAULT_LABELS.get(key, key.title()))
        add(Paragraph(_x(label.upper()), st["sec"]))
        if d.section_rule:
            add(HRFlowable(width="100%", thickness=d.section_rule_width,
                           color=C(d.section_rule), spaceBefore=1, spaceAfter=4))
        else:
            add(Spacer(1, 3))

    # Header ---------------------------------------------------------------
    header_flow: list = []
    name = data.full_name or "Resume"
    header_flow.append(Paragraph(_x(name.upper() if d.name_upper else name), st["name"]))
    if data.headline:
        header_flow.append(Paragraph(_x(data.headline), st["headline"]))
    contact = data.contact_parts()
    if contact:
        header_flow.append(Paragraph(_x(d.contact_sep.join(contact)), st["contact"]))

    page_w, page_h = letter
    m_t, m_b, m_l, m_r = d.margins
    frame_w = page_w - m_l - m_r
    band_h = 0.0
    if band:
        h = sum(f.wrap(frame_w, page_h)[1] + f.getSpaceBefore() for f in header_flow)
        band_h = m_t + h + 16

    def render_header():
        if d.header_bar:
            t = Table([[header_flow]], colWidths=[frame_w])
            t.setStyle(TableStyle([
                ("LINEBEFORE", (0, 0), (0, 0), 3.5, accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            add(t)
        else:
            story.extend(header_flow)
        if band:
            add(Spacer(1, 18))
        elif d.header_rule:
            add(HRFlowable(width="100%", thickness=0.6, color=C(d.header_rule),
                           spaceBefore=6, spaceAfter=2))
        else:
            add(Spacer(1, 4))

    # Sections -------------------------------------------------------------
    def render_summary():
        if not data.summary:
            return
        section_head("summary")
        add(Paragraph(_x(_truncate_summary(data.summary, d.summary_max)), st["body"]))

    def render_skills():
        skills = data.skills[: d.skills_max] if d.skills_max else data.skills
        if not skills:
            return
        section_head("skills")
        parts = [f"<b>{_x(s['name'])}</b>" if data.matches_jd(s["name"]) else _x(s["name"])
                 for s in skills]
        add(Paragraph("  ·  ".join(parts), st["center"] if d.skills_centered else st["body"]))

    def render_experience():
        if not data.experiences:
            return
        section_head("experience")
        for e in data.experiences:
            role, company = e.get("role") or "", e.get("company") or ""
            left = f"<b>{_x(role)}</b>"
            if company:
                left += f"{_x(d.role_company_sep)}{company_markup(company)}"
            block: list = []
            story_len = len(story)
            title_row(left, _fmt_range(e.get("start_date"), e.get("end_date")))
            for b in _bullets(e.get("description"), 5):
                add(Paragraph(_x(b), st["bullet"], bulletText="•"))
            add(Spacer(1, 4))
            block = story[story_len:]
            del story[story_len:]
            add(KeepTogether(block[:3]))  # keep title with its first bullets
            story.extend(block[3:])

    def render_projects():
        projs = data.projects[: d.projects_max] if d.projects_max else data.projects
        if not projs:
            return
        section_head("projects")
        for p in projs:
            line = f"<b>{_x(p.get('name'))}</b>"
            if p.get("technologies"):
                line += f"  |  <font color='{d.accent}'>{_x(p['technologies'])}</font>"
            add(Paragraph(line, st["body"]))
            if p.get("description"):
                add(Paragraph(_x(p["description"]), st["body"]))
            add(Spacer(1, 3))

    def render_education():
        if not data.educations:
            return
        section_head("education")
        for e in data.educations:
            title_row(f"<b>{_x(e.get('institution'))}</b>",
                      _fmt_range(e.get("start_date"), e.get("end_date")))
            deg = ", ".join(x for x in (e.get("degree"), e.get("field_of_study")) if x)
            if deg:
                add(Paragraph(_x(deg), st["sub"]))
            add(Spacer(1, 3))

    def render_certifications():
        if not data.certifications:
            return
        section_head("certifications")
        for c in data.certifications:
            line = f"<b>{_x(c.get('name'))}</b>"
            if c.get("issuer"):
                line += f" — {_x(c['issuer'])}"
            if c.get("issue_date"):
                line += f" ({_x(_fmt_date(c['issue_date']))})"
            add(Paragraph(line, st["body"]))

    renderers = {
        "header": render_header, "summary": render_summary, "skills": render_skills,
        "experience": render_experience, "projects": render_projects,
        "education": render_education, "certifications": render_certifications,
    }
    for key in order:
        renderers[key]()

    def first_page(canvas, _doc):
        canvas.setTitle(f"{data.full_name or 'Resume'} — Resume")
        canvas.setAuthor(data.full_name or "")
        if band:
            canvas.saveState()
            canvas.setFillColor(accent)
            canvas.rect(0, page_h - band_h, page_w, band_h, stroke=0, fill=1)
            canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=m_l, rightMargin=m_r,
                            topMargin=m_t, bottomMargin=m_b,
                            title=f"{data.full_name or 'Resume'} — Resume")
    doc.build(story, onFirstPage=first_page)
    return buf.getvalue()


# ── DOCX ───────────────────────────────────────────────────────────────────

def _build_docx(data: _ResumeData, template: ResolvedTemplate) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    d, order, labels = _design_for(template)
    font = "Georgia" if d.serif else "Calibri"
    rgb = lambda h: RGBColor.from_string(h.lstrip("#").upper()[:6].rjust(6, "0"))  # noqa: E731
    ACCENT, INK, BODY, MUTED = rgb(d.accent), rgb(d.ink), rgb(d.body), rgb(d.muted)
    WHITE, BAND_SUB = RGBColor(0xFF, 0xFF, 0xFF), RGBColor(0xE2, 0xE8, 0xF0)
    bs, hs = d.body_size + 0.5, d.heading_size + 0.5   # Word renders a touch smaller
    centered = d.header_align == "centered"

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    m_t, m_b, m_l, m_r = d.margins
    sec.top_margin, sec.bottom_margin = Inches(m_t / 72), Inches(m_b / 72)
    sec.left_margin, sec.right_margin = Inches(m_l / 72), Inches(m_r / 72)
    text_width = sec.page_width - sec.left_margin - sec.right_margin

    normal = doc.styles["Normal"]
    normal.font.name = font
    normal.font.size = Pt(bs)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), font)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = d.line_height * 0.9

    doc.core_properties.title = f"{data.full_name or 'Resume'} — Resume"
    doc.core_properties.author = data.full_name or ""

    def run(p, text, *, bold=False, italic=False, color=None, size=None, spacing=None):
        r = p.add_run(text)
        r.bold, r.italic = bold, italic
        if color is not None:
            r.font.color.rgb = color
        if size is not None:
            r.font.size = Pt(size)
        if spacing:
            rpr = r._r.get_or_add_rPr()
            sp = OxmlElement("w:spacing")
            sp.set(qn("w:val"), str(int(spacing * 20)))
            rpr.append(sp)
        return r

    def border(p, side: str, color: str, size_eighths: int, space: int = 1):
        ppr = p._p.get_or_add_pPr()
        bdr = ppr.find(qn("w:pBdr"))
        if bdr is None:
            bdr = OxmlElement("w:pBdr")
            ppr.append(bdr)
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(size_eighths))
        el.set(qn("w:space"), str(space))
        el.set(qn("w:color"), color.lstrip("#"))
        bdr.append(el)

    def shade(p, color: str):
        ppr = p._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), color.lstrip("#"))
        ppr.append(shd)

    def para(align=None, before=0, after=0, indent_left=None):
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_before, pf.space_after = Pt(before), Pt(after)
        if align == "center":
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if indent_left is not None:
            pf.left_indent = indent_left
        return p

    def title_line(left_parts: list[tuple[str, dict]], right: str):
        """Title on the left, dates right-aligned via a tab stop (no tables)."""
        p = para(before=3)
        p.paragraph_format.tab_stops.add_tab_stop(text_width, WD_TAB_ALIGNMENT.RIGHT)
        p.paragraph_format.keep_with_next = True
        for text, kw in left_parts:
            run(p, text, **kw)
        if right:
            run(p, "\t" + right, color=MUTED, size=bs - 1)
        return p

    def section_head(key: str):
        label = labels.get(key, _DEFAULT_LABELS.get(key, key.title()))
        p = para(align="center" if d.section_align == "center" else None,
                 before=d.section_spacing, after=3)
        p.paragraph_format.keep_with_next = True
        run(p, label.upper(), bold=True, color=ACCENT, size=hs,
            spacing=1.5 if d.section_align == "center" else 0.6)
        if d.section_rule:
            border(p, "bottom", d.section_rule, max(2, int(d.section_rule_width * 6)))

    # Header ---------------------------------------------------------------
    def render_header():
        band = d.header_band
        align = "center" if centered else None
        name = data.full_name or "Resume"
        lines: list = []
        p = para(align=align)
        run(p, name.upper() if d.name_upper else name, bold=d.name_bold,
            color=WHITE if band else INK, size=d.name_size,
            spacing=2.5 if d.name_upper else None)
        lines.append(p)
        if data.headline:
            p = para(align=align, before=2)
            run(p, data.headline, italic=d.headline_italic, bold=not d.serif,
                color=BAND_SUB if band else (ACCENT if d.family == "modern" else MUTED), size=bs + 1)
            lines.append(p)
        contact = data.contact_parts()
        if contact:
            p = para(align=align, before=3)
            run(p, d.contact_sep.join(contact), color=BAND_SUB if band else MUTED, size=bs - 0.5)
            lines.append(p)
        if band:
            for i, p in enumerate(lines):
                shade(p, d.accent)
                p.paragraph_format.left_indent = Inches(0.12)
                p.paragraph_format.right_indent = Inches(0.12)
            lines[0].paragraph_format.space_before = Pt(0)
            # padding inside the band
            for ln in (lines[0], lines[-1]):
                border(ln, "top" if ln is lines[0] else "bottom", d.accent, 24, 6)
            para(after=6)
        elif d.header_bar:
            for p in lines:
                border(p, "left", d.accent, 24, 8)
            para(after=4)
        elif d.header_rule:
            border(lines[-1], "bottom", d.header_rule, 4, 6)
            para(after=2)
        else:
            para(after=2)

    def render_summary():
        if not data.summary:
            return
        section_head("summary")
        p = para()
        run(p, _truncate_summary(data.summary, d.summary_max), color=BODY)

    def render_skills():
        skills = data.skills[: d.skills_max] if d.skills_max else data.skills
        if not skills:
            return
        section_head("skills")
        p = para(align="center" if d.skills_centered else None)
        for i, s in enumerate(skills):
            run(p, s["name"], bold=data.matches_jd(s["name"]), color=BODY)
            if i < len(skills) - 1:
                run(p, "  ·  ", color=MUTED)

    def render_experience():
        if not data.experiences:
            return
        section_head("experience")
        for e in data.experiences:
            parts: list[tuple[str, dict]] = [(e.get("role") or "", {"bold": True, "color": INK})]
            if e.get("company"):
                parts.append((d.role_company_sep, {"color": MUTED}))
                parts.append((e["company"], {
                    "italic": d.company_italic,
                    "color": ACCENT if d.family in ("modern", "sidebar") else INK,
                }))
            title_line(parts, _fmt_range(e.get("start_date"), e.get("end_date")))
            for b in _bullets(e.get("description"), 5):
                p = doc.add_paragraph(style="List Bullet")
                p.paragraph_format.space_after = Pt(1)
                run(p, b, color=BODY)
            para(after=2)

    def render_projects():
        projs = data.projects[: d.projects_max] if d.projects_max else data.projects
        if not projs:
            return
        section_head("projects")
        for pr in projs:
            p = para(before=2)
            run(p, pr.get("name") or "", bold=True, color=INK)
            if pr.get("technologies"):
                run(p, f"  |  {pr['technologies']}", color=ACCENT, size=bs - 0.5)
            if pr.get("description"):
                q = para(after=2)
                run(q, pr["description"], color=BODY)

    def render_education():
        if not data.educations:
            return
        section_head("education")
        for e in data.educations:
            title_line([(e.get("institution") or "", {"bold": True, "color": INK})],
                       _fmt_range(e.get("start_date"), e.get("end_date")))
            deg = ", ".join(x for x in (e.get("degree"), e.get("field_of_study")) if x)
            if deg:
                p = para(after=2)
                run(p, deg, italic=d.serif, color=MUTED)

    def render_certifications():
        if not data.certifications:
            return
        section_head("certifications")
        for c in data.certifications:
            p = para(after=1)
            run(p, c.get("name") or "", bold=True, color=INK)
            if c.get("issuer"):
                run(p, f" — {c['issuer']}", color=BODY)
            if c.get("issue_date"):
                run(p, f" ({_fmt_date(c['issue_date'])})", color=MUTED)

    renderers = {
        "header": render_header, "summary": render_summary, "skills": render_skills,
        "experience": render_experience, "projects": render_projects,
        "education": render_education, "certifications": render_certifications,
    }
    for key in order:
        renderers[key]()

    # python-docx starts with one empty paragraph — drop it.
    first = doc.paragraphs[0]
    if not first.text and first._p.getprevious() is None and len(doc.paragraphs) > 1:
        first._p.getparent().remove(first._p)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── Public API ─────────────────────────────────────────────────────────────

class ResumeExporter:
    """
    Export the user's resume as PDF or DOCX using a resolved template.

    Callers MUST pass a ResolvedTemplate (from ResumeTemplateResolver); its
    base_template picks the design, its config applies the user's edits.
    """

    def __init__(self, db: Session, user: User, template: ResolvedTemplate,
                 app_id: int | None = None):
        self.db = db
        self.user = user
        self.template = template
        self.app_id = app_id

    def _load(self) -> _ResumeData:
        return _ResumeData(self.db, self.user.id, self.user.email, app_id=self.app_id)

    def as_pdf(self) -> bytes:
        try:
            return _build_pdf(self._load(), self.template)
        except Exception as exc:
            logger.error("resume_pdf_error", error=str(exc), template=self.template.base_template)
            raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")

    def as_docx(self) -> bytes:
        try:
            return _build_docx(self._load(), self.template)
        except Exception as exc:
            logger.error("resume_docx_error", error=str(exc), template=self.template.base_template)
            raise HTTPException(status_code=500, detail=f"DOCX generation failed: {exc}")

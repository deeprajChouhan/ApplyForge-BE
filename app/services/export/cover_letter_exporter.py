"""Cover-letter export — PDF (reportlab) and DOCX (python-docx).

Backs POST /applications/{id}/export/cover-letter. Takes the newest
non-deleted generated cover letter for the application, fills the sender
block from the same identity merge the resume exporter uses (parsed resume
wins, DB profile is the fallback), adds the recipient details the user typed
in the export modal, and replaces any [placeholder] tokens the LLM left in
the body. Content is never rewritten beyond placeholder substitution, so the
grounded text the hallucination checker evaluated is what gets exported.
"""
from __future__ import annotations

import io
import re
from datetime import date
from xml.sax.saxutils import escape as _xml_escape

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.enums import DocumentType
from app.models.models import GeneratedDocument, JobApplication, User
from app.schemas.application import CoverLetterExportRequest
from app.services.export.resume_exporter import _ResumeData

_SALUTATION_RE = re.compile(r"^\s*(dear|to whom it may concern|hello|hi)\b", re.I)
_PLACEHOLDER_RE = re.compile(r"\[([^\[\]\n]{1,60})\]")
_MD_RE = re.compile(r"(\*\*|__|\*|`|^#+\s*)", re.M)


def _x(text: str) -> str:
    return _xml_escape(text or "")


class CoverLetterExporter:
    def __init__(self, db: Session, user: User, app_id: int, req: CoverLetterExportRequest):
        self.db = db
        self.user = user
        self.req = req
        app = db.get(JobApplication, app_id)
        if app is None or app.user_id != user.id or app.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Application not found")
        self.app = app

        doc = (
            db.query(GeneratedDocument)
            .filter(
                GeneratedDocument.application_id == app_id,
                GeneratedDocument.doc_type == DocumentType.cover_letter,
                GeneratedDocument.deleted_at.is_(None),
            )
            .order_by(GeneratedDocument.version.desc())
            .first()
        )
        if doc is None or not (doc.content or "").strip():
            raise HTTPException(status_code=404, detail="No cover letter generated for this application yet")

        self.me = _ResumeData(db, user.id, user.email or "", app_id=app_id)
        self.paragraphs = self._prepare(doc.content)

    # ── content ────────────────────────────────────────────────────────────
    def _fill(self, key: str) -> str | None:
        k = key.lower().strip()
        me, req, app = self.me, self.req, self.app
        # Most specific first: "[Company Name]" must hit company, not name.
        table = [
            (("company address",), req.company_address or ""),
            (("recipient title", "title of recipient"), req.recipient_title or ""),
            (("hiring manager", "recipient", "manager"), req.recipient_name),
            (("company", "organisation", "organization", "employer"), app.company_name),
            (("position", "role", "job title"), app.role_title),
            (("date", "today"), date.today().strftime("%d %B %Y")),
            (("email",), me.email),
            (("phone", "mobile", "contact number"), me.phone),
            (("linkedin",), me.linkedin),
            (("website", "portfolio"), me.website),
            (("address", "location", "city"), me.location),
            (("your name", "full name", "candidate name", "name"), me.full_name),
        ]
        for needles, value in table:
            if any(n in k for n in needles):
                return value or None
        return None  # unknown placeholder → keep as-is so the user sees it

    def _prepare(self, content: str) -> list[str]:
        text = content.replace("\r\n", "\n")
        text = _MD_RE.sub("", text)
        text = _PLACEHOLDER_RE.sub(lambda m: self._fill(m.group(1)) or m.group(0), text)
        paras = [re.sub(r"[ \t]*\n[ \t]*", "\n", p).strip() for p in re.split(r"\n\s*\n", text)]
        paras = [p for p in paras if p]
        # Ensure a salutation exists and addresses the chosen recipient.
        if not paras or not _SALUTATION_RE.match(paras[0]):
            paras.insert(0, f"Dear {self.req.recipient_name or 'Hiring Manager'},")
        return paras

    def _sender_lines(self) -> list[str]:
        me = self.me
        lines = [me.full_name] if me.full_name else []
        contact = " | ".join(p for p in (me.email, me.phone, me.location, me.linkedin) if p)
        if contact:
            lines.append(contact)
        return lines

    def _recipient_lines(self) -> list[str]:
        r = self.req
        lines = [r.recipient_name or "Hiring Manager"]
        if r.recipient_title:
            lines.append(r.recipient_title)
        lines.append(self.app.company_name)
        if r.company_address:
            lines.extend(l.strip() for l in r.company_address.splitlines() if l.strip())
        return lines

    def filename(self, ext: str) -> str:
        base = re.sub(r"[^A-Za-z0-9]+", "_", f"{self.me.full_name or 'cover'}_{self.app.company_name}").strip("_")
        return f"{base or 'cover'}_cover_letter.{ext}"

    # ── renderers ──────────────────────────────────────────────────────────
    def as_pdf(self) -> bytes:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm,
                                topMargin=20 * mm, bottomMargin=20 * mm,
                                title=f"Cover letter — {self.app.company_name}", author=self.me.full_name)
        name = ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=16, leading=20)
        small = ParagraphStyle("small", fontName="Helvetica", fontSize=9.5, leading=13)
        body = ParagraphStyle("body", fontName="Helvetica", fontSize=11, leading=15, spaceAfter=9)

        story: list = []
        sender = self._sender_lines()
        if sender:
            story.append(Paragraph(_x(sender[0]), name))
            for l in sender[1:]:
                story.append(Paragraph(_x(l), small))
            story.append(Spacer(1, 8 * mm))
        story.append(Paragraph(_x(date.today().strftime("%d %B %Y")), small))
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph("<br/>".join(_x(l) for l in self._recipient_lines()), small))
        story.append(Spacer(1, 7 * mm))
        for p in self.paragraphs:
            story.append(Paragraph(_x(p).replace("\n", "<br/>"), body))
        doc.build(story)
        return buf.getvalue()

    def as_docx(self) -> bytes:
        from docx import Document
        from docx.shared import Pt

        d = Document()
        normal = d.styles["Normal"]
        normal.font.name = "Calibri"
        normal.font.size = Pt(11)

        def para(text: str, *, bold=False, size=None, after=6):
            p = d.add_paragraph()
            p.paragraph_format.space_after = Pt(after)
            lines = text.split("\n")
            for i, line in enumerate(lines):
                r = p.add_run(line)
                r.bold = bold
                if size:
                    r.font.size = Pt(size)
                if i < len(lines) - 1:
                    r.add_break()
            return p

        sender = self._sender_lines()
        if sender:
            para(sender[0], bold=True, size=16, after=0)
            for l in sender[1:]:
                para(l, size=9.5, after=0)
            para("", after=6)
        para(date.today().strftime("%d %B %Y"), after=10)
        para("\n".join(self._recipient_lines()), after=14)
        for p in self.paragraphs:
            para(p, after=9)
        buf = io.BytesIO()
        d.save(buf)
        return buf.getvalue()

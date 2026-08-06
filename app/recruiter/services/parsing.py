"""
CV parsing: uploaded CV bytes (PDF/DOCX/TXT) → structured fields for a
CandidateProfile. Heuristic and dependency-light (reuses pypdf/python-docx which
the backend already depends on) so it runs offline; an LLM parse can slot into
the raw-text → fields seam later for higher fidelity.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from app.recruiter.services.skills import extract_skills

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?){2,4}\d{2,4})")
_YOE_RE = re.compile(r"(\d{1,2})\+?\s*years?", re.IGNORECASE)


@dataclass
class ParsedCV:
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    headline: str | None = None
    location: str | None = None
    years_experience: float | None = None
    summary: str | None = None
    skills: list[str] = field(default_factory=list)
    raw_text: str = ""


def extract_text(content: bytes, filename: str) -> str:
    name = (filename or "").lower()
    try:
        if name.endswith(".pdf"):
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        if name.endswith(".docx"):
            from docx import Document

            doc = Document(io.BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs)
    except Exception:
        pass
    return content.decode("utf-8", errors="ignore")


def _guess_name(lines: list[str], email: str | None) -> str | None:
    for line in lines[:6]:
        s = line.strip()
        if not s or "@" in s or any(ch.isdigit() for ch in s):
            continue
        words = s.split()
        if 1 < len(words) <= 4 and s[0].isalpha():
            return s
    if email:
        local = email.split("@", 1)[0]
        return local.replace(".", " ").replace("_", " ").title()
    return None


def parse_cv(content: bytes, filename: str) -> ParsedCV:
    text = extract_text(content, filename)
    lines = [ln for ln in text.splitlines() if ln.strip()]

    email_match = _EMAIL_RE.search(text)
    email = email_match.group(0) if email_match else None

    phone_match = _PHONE_RE.search(text)
    phone = phone_match.group(0).strip() if phone_match else None

    yoe = None
    yoe_match = _YOE_RE.search(text)
    if yoe_match:
        try:
            yoe = float(yoe_match.group(1))
        except ValueError:
            yoe = None

    skills = extract_skills(text)
    name = _guess_name(lines, email)

    headline = None
    for line in lines[1:5]:
        s = line.strip()
        if s and "@" not in s and s != name and len(s) < 120:
            headline = s
            break

    summary = " ".join(lines[:8])[:600] if lines else None

    return ParsedCV(
        full_name=name,
        email=email,
        phone=phone,
        headline=headline,
        location=None,
        years_experience=yoe,
        summary=summary,
        skills=skills,
        raw_text=text,
    )

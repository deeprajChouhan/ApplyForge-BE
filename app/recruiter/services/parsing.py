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
from datetime import date

from app.recruiter.services.llm_parse import llm_parse_cv
from app.recruiter.services.skills import extract_skills, normalize_skill

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?){2,4}\d{2,4})")
_YOE_RE = re.compile(r"(\d{1,2})\+?\s*years?", re.IGNORECASE)


@dataclass
class ParsedExperience:
    title: str | None = None
    company: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    description: str | None = None


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
    seniority: str | None = None
    experiences: list[ParsedExperience] = field(default_factory=list)
    parsed_by_llm: bool = False


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


def _heuristic_parse(text: str) -> ParsedCV:
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


def _coerce_date(value) -> date | None:
    """Loosely parse 'YYYY', 'YYYY-MM', or 'YYYY-MM-DD' into a date; else None."""
    if not value or not isinstance(value, str):
        return None
    m = re.match(r"\s*(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", value.strip())
    if not m:
        return None
    try:
        year = int(m.group(1))
        month = int(m.group(2)) if m.group(2) else 1
        day = int(m.group(3)) if m.group(3) else 1
        month = min(max(month, 1), 12)
        day = min(max(day, 1), 28)
        return date(year, month, day)
    except (ValueError, TypeError):
        return None


def _clean_str(value) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _merge_skills(heuristic: list[str], llm_skills) -> list[str]:
    """Normalise + union skills from both sources so any named skill counts."""
    out: list[str] = []
    seen: set[str] = set()
    for source in (llm_skills or [], heuristic or []):
        for raw in source:
            if not isinstance(raw, str):
                continue
            norm = normalize_skill(raw)
            if norm and norm not in seen:
                seen.add(norm)
                out.append(norm)
    return out


def _apply_llm(base: ParsedCV, data: dict) -> ParsedCV:
    """Overlay trusted LLM fields onto the heuristic baseline."""
    base.full_name = _clean_str(data.get("full_name")) or base.full_name
    base.email = _clean_str(data.get("email")) or base.email
    base.phone = _clean_str(data.get("phone")) or base.phone
    base.headline = _clean_str(data.get("headline")) or base.headline
    base.location = _clean_str(data.get("location")) or base.location
    base.seniority = _clean_str(data.get("seniority")) or base.seniority
    base.summary = _clean_str(data.get("summary")) or base.summary

    yoe = data.get("years_experience")
    if isinstance(yoe, (int, float)) and yoe >= 0:
        base.years_experience = float(yoe)

    base.skills = _merge_skills(base.skills, data.get("skills"))

    experiences: list[ParsedExperience] = []
    for item in data.get("experiences") or []:
        if not isinstance(item, dict):
            continue
        experiences.append(
            ParsedExperience(
                title=_clean_str(item.get("title")),
                company=_clean_str(item.get("company")),
                start_date=_coerce_date(item.get("start_date")),
                end_date=_coerce_date(item.get("end_date")),
                description=_clean_str(item.get("description")),
            )
        )
    base.experiences = experiences
    base.parsed_by_llm = True
    return base


def parse_cv(content: bytes, filename: str) -> ParsedCV:
    """
    Parse a CV into structured fields. Runs the fast heuristic as a baseline,
    then — when a real LLM is configured — overlays a richer structured parse
    (real skills, dated work history, seniority). Falls back cleanly to the
    heuristic whenever the LLM is unavailable or its output can't be trusted.
    """
    text = extract_text(content, filename)
    parsed = _heuristic_parse(text)
    parsed.raw_text = text

    data = llm_parse_cv(text)
    if isinstance(data, dict):
        try:
            parsed = _apply_llm(parsed, data)
        except Exception:
            pass  # keep the heuristic result if the overlay misbehaves
    return parsed

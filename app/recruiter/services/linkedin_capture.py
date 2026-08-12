"""
LinkedIn profile capture — one-click ingestion from the recruiter Chrome
extension. The extension scrapes a public /in/<slug> page and POSTs a JSON
payload; we normalise it into a CandidateProfile with skills and dated work
experiences, embed it, and (optionally) attach it to a role's pipeline.

Dedup: within an agency, the same linkedin_url updates the existing profile
in-place instead of creating a second row. That way re-clicking is idempotent
and, when a recruiter re-captures a profile after the candidate updated their
LinkedIn, we refresh the pool copy without losing history.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.recruiter.enums import ApplicationStage, CandidateSource
from app.recruiter.models import (
    Application,
    CandidateExperience,
    CandidateProfile,
    CandidateSkill,
    Role,
)
from app.recruiter.services.matching import embed_candidate
from app.recruiter.services.skills import extract_skills, normalize_skill


_LINKEDIN_HOST_RE = re.compile(r"^([a-z]{2,3}\.)?linkedin\.com$", re.IGNORECASE)
_LINKEDIN_PATH_RE = re.compile(r"^/in/([^/?#]+)/?$", re.IGNORECASE)
_YOE_RE = re.compile(r"(\d{1,2})\+?\s*years?", re.IGNORECASE)


@dataclass
class CapturedCandidate:
    candidate_id: int
    full_name: str | None
    email: str | None
    linkedin_url: str
    skill_count: int
    created: bool                 # True on insert, False when we deduped/refreshed
    application_id: int | None    # set when role_id was provided


def canonicalize_linkedin_url(raw: str | None) -> str | None:
    """
    Reduce any LinkedIn profile URL to the canonical form used for dedup:
      https://www.linkedin.com/in/<slug>
    Strips query strings, fragments, trailing slashes, and country subdomains
    (uk.linkedin.com/in/... → www.linkedin.com/in/...). Returns None when the
    input isn't a public /in/ profile URL.
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return None
    host = (parts.netloc or "").lower()
    if not _LINKEDIN_HOST_RE.match(host):
        return None
    m = _LINKEDIN_PATH_RE.match(parts.path or "")
    if not m:
        return None
    slug = m.group(1).lower()
    return urlunsplit(("https", "www.linkedin.com", f"/in/{slug}", "", ""))


def _clean(value) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _coerce_date(value) -> date | None:
    """Loosely parse 'YYYY', 'YYYY-MM', or 'YYYY-MM-DD' — LinkedIn only exposes
    year+month, so we default missing day to 1."""
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


def _derive_years(experiences: list[dict], about_text: str | None) -> float | None:
    """Sum experience durations; fall back to '<n> years' in the About blurb."""
    total_months = 0
    counted = False
    today = date.today()
    for exp in experiences or []:
        if not isinstance(exp, dict):
            continue
        start = _coerce_date(exp.get("start_date"))
        end = _coerce_date(exp.get("end_date")) or today
        if not start:
            continue
        months = (end.year - start.year) * 12 + (end.month - start.month)
        if months > 0:
            total_months += months
            counted = True
    if counted:
        return round(total_months / 12.0, 1)
    if about_text:
        m = _YOE_RE.search(about_text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


def _merge_skill_sources(explicit: list, derived: list[str]) -> list[str]:
    """Normalise + union skills from the LinkedIn "Skills" section and any
    skills we can extract from the free-text about/headline/experience blobs."""
    out: list[str] = []
    seen: set[str] = set()
    for source in (explicit or [], derived or []):
        for raw in source:
            if not isinstance(raw, str):
                continue
            norm = normalize_skill(raw)
            if norm and norm not in seen:
                seen.add(norm)
                out.append(norm)
    return out


def _raw_text_blob(payload: dict, experiences: list[dict]) -> str:
    """A single searchable text blob for candidate_chat/matching to grep."""
    lines: list[str] = []
    for key in ("full_name", "headline", "location", "about"):
        v = _clean(payload.get(key))
        if v:
            lines.append(v)
    for exp in experiences or []:
        if not isinstance(exp, dict):
            continue
        title = _clean(exp.get("title")) or ""
        company = _clean(exp.get("company")) or ""
        if title or company:
            lines.append(f"{title} at {company}".strip())
        desc = _clean(exp.get("description"))
        if desc:
            lines.append(desc)
    return "\n".join(lines)


def capture_linkedin_profile(
    db: Session,
    agency_id: int,
    payload: dict,
    role: Role | None = None,
) -> CapturedCandidate:
    """
    Persist a scraped LinkedIn profile as a CandidateProfile. Returns the
    capture result with `created=False` when we updated an existing row (same
    agency + same canonical linkedin_url). When `role` is supplied, also
    creates a tracking `Application` at stage `sourced` (idempotent — a second
    capture for the same role reuses the existing application).
    """
    canonical_url = canonicalize_linkedin_url(payload.get("linkedin_url"))
    if not canonical_url:
        raise ValueError("linkedin_url must be a public linkedin.com/in/<slug> URL")

    experiences = payload.get("experiences") or []
    if not isinstance(experiences, list):
        experiences = []

    explicit_skills = payload.get("skills") or []
    if not isinstance(explicit_skills, list):
        explicit_skills = []
    about = _clean(payload.get("about"))
    headline = _clean(payload.get("headline"))
    derived_skills = extract_skills(" ".join(filter(None, [about, headline, _raw_text_blob(payload, experiences)])))
    skills = _merge_skill_sources(explicit_skills, derived_skills)

    # Look up an existing pool entry for this agency + URL — the dedup key.
    existing = (
        db.query(CandidateProfile)
        .filter(
            CandidateProfile.agency_id == agency_id,
            CandidateProfile.linkedin_url == canonical_url,
        )
        .one_or_none()
    )

    created = existing is None
    profile = existing or CandidateProfile(
        agency_id=agency_id,
        source=CandidateSource.linkedin,
        linkedin_url=canonical_url,
    )

    # Update-in-place semantics: recapturing refreshes the profile fields
    # from the latest scrape but never clobbers with an empty string.
    profile.source = profile.source or CandidateSource.linkedin
    profile.full_name = _clean(payload.get("full_name")) or profile.full_name
    profile.email = _clean(payload.get("email")) or profile.email
    profile.phone = _clean(payload.get("phone")) or profile.phone
    profile.headline = headline or profile.headline
    profile.location = _clean(payload.get("location")) or profile.location
    profile.summary = about or profile.summary
    profile.linkedin_url = canonical_url

    years = _derive_years(experiences, about)
    if years is not None:
        profile.years_experience = years

    # Keep the concatenated text blob available for candidate_chat's grounded
    # QA and matching's fallback bag-of-words path.
    profile.raw_cv_text = _raw_text_blob(payload, experiences)

    if created:
        db.add(profile)
    db.flush()  # need profile.id for children

    # Skills — replace wholesale on recapture (LinkedIn is the source of truth
    # for this profile; stale skills should not linger).
    if not created:
        db.query(CandidateSkill).filter(CandidateSkill.candidate_id == profile.id).delete()
    for skill in skills:
        db.add(CandidateSkill(candidate_id=profile.id, name=skill))

    # Same policy for experiences — the LinkedIn history overwrites what we
    # stored last time.
    if not created:
        db.query(CandidateExperience).filter(
            CandidateExperience.candidate_id == profile.id
        ).delete()
    for exp in experiences:
        if not isinstance(exp, dict):
            continue
        db.add(
            CandidateExperience(
                candidate_id=profile.id,
                title=_clean(exp.get("title")),
                company=_clean(exp.get("company")),
                start_date=_coerce_date(exp.get("start_date")),
                end_date=_coerce_date(exp.get("end_date")),
                description=_clean(exp.get("description")),
            )
        )

    db.flush()
    db.refresh(profile)
    profile.embedding = embed_candidate(profile)

    application_id: int | None = None
    if role is not None:
        # Idempotent role attach: don't stack duplicate Applications for the
        # same role. If one already exists, we just return its id.
        existing_app = (
            db.query(Application)
            .filter(
                Application.agency_id == agency_id,
                Application.role_id == role.id,
                Application.candidate_id == profile.id,
            )
            .one_or_none()
        )
        if existing_app is None:
            app_row = Application(
                agency_id=agency_id,
                role_id=role.id,
                candidate_id=profile.id,
                stage=ApplicationStage.sourced,
            )
            db.add(app_row)
            db.flush()
            application_id = app_row.id
        else:
            application_id = existing_app.id

    db.commit()
    db.refresh(profile)

    return CapturedCandidate(
        candidate_id=profile.id,
        full_name=profile.full_name,
        email=profile.email,
        linkedin_url=canonical_url,
        skill_count=len(skills),
        created=created,
        application_id=application_id,
    )

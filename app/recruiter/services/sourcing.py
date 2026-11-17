"""
Sourced stage — the holding area between "found on LinkedIn" and "in the pool".

Flow:
  1. The Chrome extension scrapes a linkedin.com/in/<slug> page and asks for a
     fit score against the role the recruiter is sourcing for
     (`score_preview` — nothing is stored).
  2. "Add to Sourced" stores everything we could scrape as a SourcedProfile
     (`save_sourced`, deduped per agency on canonical LinkedIn URL) with the
     score at capture time.
  3. In the web app the recruiter adds notes and documents (CV, cover
     letter); each CV's text is folded into the score (`rescore`).
  4. `promote` copies the profile into the candidate pool through the same
     path as the old one-click capture, merges in the parsed CV, moves the
     documents onto the pool candidate and — when asked — attaches it to the
     role's pipeline.

Unvetted people never reach matching, shortlists or Ask-the-pool because
they live in their own table until promoted. Scores use the pool matcher
(`score_candidate`) unchanged, so a sourced score and a pool score for the
same evidence are the same number.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.recruiter.models import (
    CandidateDocument,
    CandidateExperience,
    CandidateProfile,
    CandidateSkill,
    Role,
    SourcedDocument,
    SourcedProfile,
)
from app.recruiter.services import documents as documents_service
from app.recruiter.services.linkedin_capture import (
    _clean,
    _coerce_date,
    _derive_years,
    _merge_skill_sources,
    _raw_text_blob,
    canonicalize_linkedin_url,
    capture_linkedin_profile,
)
from app.recruiter.services.matching import MatchResult, embed_candidate, embed_role, score_candidate
from app.recruiter.services.parsing import extract_text, parse_cv
from app.recruiter.services.skills import extract_skills, normalize_skill
from app.services.storage.service import get_storage_service

logger = logging.getLogger(__name__)

STATUS_SOURCED = "sourced"
STATUS_PROMOTED = "promoted"
STATUS_ARCHIVED = "archived"
STATUSES = {STATUS_SOURCED, STATUS_PROMOTED, STATUS_ARCHIVED}

PAGE_TEXT_CAP = 20_000
DOC_TEXT_CAP = 40_000
DOC_KINDS = {"cv", "cover_letter", "other"}


class SourcingError(ValueError):
    """Caller-safe validation error."""


# ── payload normalisation ───────────────────────────────────────────────────

def _list_of_dicts(value) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _list_of_str(value, cap: int = 200) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for v in value:
        s = _clean(v)
        if s and len(s) <= cap and s not in out:
            out.append(s)
    return out


def _skills_for(payload: dict, extra_text: str = "") -> list[str]:
    experiences = _list_of_dicts(payload.get("experiences"))
    blob = " ".join(
        filter(None, [_clean(payload.get("about")), _clean(payload.get("headline")),
                      _raw_text_blob(payload, experiences), extra_text])
    )
    return _merge_skill_sources(_list_of_str(payload.get("skills"), 80), extract_skills(blob))


def _transient_candidate(payload: dict, extra_text: str = "") -> CandidateProfile:
    """Build an unsaved CandidateProfile so the pool matcher can score it.
    Never added to a session."""
    experiences = _list_of_dicts(payload.get("experiences"))
    about = _clean(payload.get("about"))
    raw = "\n".join(filter(None, [_raw_text_blob(payload, experiences), extra_text]))
    cand = CandidateProfile(
        headline=_clean(payload.get("headline")),
        summary=about,
        years_experience=_derive_years(experiences, about),
        raw_cv_text=raw,
    )
    cand.skills = [CandidateSkill(name=s) for s in _skills_for(payload, extra_text)]
    cand.experiences = [
        CandidateExperience(
            title=_clean(e.get("title")),
            company=_clean(e.get("company")),
            start_date=_coerce_date(e.get("start_date")),
            end_date=_coerce_date(e.get("end_date")),
            description=_clean(e.get("description")),
        )
        for e in experiences
    ]
    return cand


def _score(role: Role, payload: dict, extra_text: str = "") -> MatchResult:
    cand = _transient_candidate(payload, extra_text)
    cand.embedding = embed_candidate(cand)
    role_vec = role.embedding or embed_role(role)
    return score_candidate(role, cand, role_vec)


def score_preview(role: Role, payload: dict) -> MatchResult:
    """Fit score for a scraped profile against a role. Stores nothing."""
    if not canonicalize_linkedin_url(payload.get("linkedin_url")):
        raise SourcingError("linkedin_url must be a public linkedin.com/in/<slug> URL")
    return _score(role, payload)


# ── persistence ─────────────────────────────────────────────────────────────

def _payload_of(sp: SourcedProfile) -> dict:
    """Rebuild the LinkedIn-capture payload from a stored sourced profile."""
    return {
        "linkedin_url": sp.linkedin_url,
        "full_name": sp.full_name,
        "headline": sp.headline,
        "location": sp.location,
        "about": sp.about,
        "email": sp.email,
        "phone": sp.phone,
        "skills": sp.skills or [],
        "experiences": sp.experiences or [],
    }


def _doc_text(sp: SourcedProfile) -> str:
    return "\n".join(d.extracted_text for d in sp.documents if d.extracted_text)


def _apply_score(sp: SourcedProfile, role: Role | None) -> None:
    if role is None:
        sp.fit_score = None
        sp.fit_reasons = None
        sp.fit_gaps = None
        sp.fit_breakdown = None
        sp.scored_at = None
        return
    extra = "\n".join(filter(None, [_doc_text(sp), sp.page_text or ""]))
    result = _score(role, _payload_of(sp), extra)
    sp.fit_score = result.fit_score
    sp.fit_reasons = result.reasons
    sp.fit_gaps = result.gaps
    sp.fit_breakdown = result.breakdown
    sp.scored_at = datetime.utcnow()


def get_sourced(db: Session, agency_id: int, sourced_id: int) -> SourcedProfile | None:
    sp = db.get(SourcedProfile, sourced_id)
    if sp is None or sp.agency_id != agency_id:
        return None
    return sp


def find_by_url(db: Session, agency_id: int, linkedin_url: str | None) -> SourcedProfile | None:
    url = canonicalize_linkedin_url(linkedin_url)
    if not url:
        return None
    return (
        db.query(SourcedProfile)
        .filter(SourcedProfile.agency_id == agency_id, SourcedProfile.linkedin_url == url)
        .one_or_none()
    )


def find_pool_candidate_id(db: Session, agency_id: int, linkedin_url: str | None) -> int | None:
    url = canonicalize_linkedin_url(linkedin_url)
    if not url:
        return None
    row = (
        db.query(CandidateProfile.id)
        .filter(CandidateProfile.agency_id == agency_id, CandidateProfile.linkedin_url == url)
        .first()
    )
    return row[0] if row else None


@dataclass
class SaveResult:
    sourced: SourcedProfile
    created: bool


def save_sourced(db: Session, agency_id: int, payload: dict, role: Role | None) -> SaveResult:
    """Upsert a scraped profile into Sourced (dedup on canonical URL).

    Re-capturing refreshes the scraped fields but never blanks a field the
    new scrape missed, and keeps notes, documents and status. An archived
    profile captured again comes back to `sourced`.
    """
    url = canonicalize_linkedin_url(payload.get("linkedin_url"))
    if not url:
        raise SourcingError("linkedin_url must be a public linkedin.com/in/<slug> URL")

    sp = find_by_url(db, agency_id, url)
    created = sp is None
    if created:
        sp = SourcedProfile(agency_id=agency_id, linkedin_url=url, status=STATUS_SOURCED)
        db.add(sp)
    elif sp.status == STATUS_ARCHIVED:
        sp.status = STATUS_SOURCED

    for field in ("full_name", "headline", "location", "about", "email", "phone"):
        v = _clean(payload.get(field))
        if v:
            setattr(sp, field, v)

    experiences = _list_of_dicts(payload.get("experiences"))
    if experiences:
        sp.experiences = [
            {k: _clean(e.get(k)) for k in ("title", "company", "start_date", "end_date", "description")}
            for e in experiences
        ]
    skills = _list_of_str(payload.get("skills"), 80)
    if skills:
        sp.skills = skills
    for field in ("education", "certifications"):
        rows = _list_of_dicts(payload.get(field))
        if rows:
            setattr(sp, field, [{k: _clean(v) for k, v in r.items() if isinstance(k, str)} for r in rows][:30])
    langs = _list_of_str(payload.get("languages"), 80)
    if langs:
        sp.languages = langs
    page_text = _clean(payload.get("page_text"))
    if page_text:
        sp.page_text = page_text[:PAGE_TEXT_CAP]

    years = _derive_years(sp.experiences or [], sp.about)
    if years is not None:
        sp.years_experience = years

    if role is not None:
        sp.role_id = role.id
    db.flush()
    db.refresh(sp)
    _apply_score(sp, role if role is not None else (db.get(Role, sp.role_id) if sp.role_id else None))
    db.commit()
    db.refresh(sp)
    return SaveResult(sourced=sp, created=created)


def list_sourced(
    db: Session, agency_id: int, status: str | None = STATUS_SOURCED, role_id: int | None = None
) -> list[SourcedProfile]:
    q = db.query(SourcedProfile).filter(SourcedProfile.agency_id == agency_id)
    if status:
        q = q.filter(SourcedProfile.status == status)
    if role_id is not None:
        q = q.filter(SourcedProfile.role_id == role_id)
    return q.order_by(SourcedProfile.id.desc()).all()


def update_sourced(db: Session, sp: SourcedProfile, changes: dict, role_lookup) -> SourcedProfile:
    """Edit notes / contact fields / target role. `role_lookup(id)` returns an
    agency-checked Role or raises."""
    rescore = False
    for field in ("notes", "email", "phone", "full_name", "headline", "location"):
        if field in changes:
            setattr(sp, field, _clean(changes[field]) if field != "notes" else (changes[field] or None))
    if "role_id" in changes:
        new_role = role_lookup(changes["role_id"]) if changes["role_id"] is not None else None
        sp.role_id = new_role.id if new_role else None
        rescore = True
    if "status" in changes and changes["status"] is not None:
        st = changes["status"]
        if st not in (STATUS_SOURCED, STATUS_ARCHIVED):
            raise SourcingError("status can only be set to 'sourced' or 'archived'")
        if sp.status == STATUS_PROMOTED:
            raise SourcingError("This profile is already in the pool")
        sp.status = st
    db.flush()
    if rescore:
        _apply_score(sp, db.get(Role, sp.role_id) if sp.role_id else None)
    db.commit()
    db.refresh(sp)
    return sp


def rescore(db: Session, sp: SourcedProfile) -> SourcedProfile:
    _apply_score(sp, db.get(Role, sp.role_id) if sp.role_id else None)
    db.commit()
    db.refresh(sp)
    return sp


# ── documents ───────────────────────────────────────────────────────────────

def add_document(db: Session, sp: SourcedProfile, filename: str | None, content: bytes, kind: str) -> SourcedDocument:
    if sp.status == STATUS_PROMOTED:
        raise SourcingError("This profile is already in the pool — add documents on the pool candidate")
    if kind not in DOC_KINDS:
        raise SourcingError("kind must be one of: cv, cover_letter, other")
    file = documents_service.validate_upload(filename, content)
    uri = get_storage_service().upload_bytes(
        content=file.content,
        key_prefix=f"recruiter/{int(sp.agency_id)}/sourced/{int(sp.id)}",
        filename=file.filename,
        content_type=file.content_type,
    )
    try:
        text = (extract_text(file.content, file.filename) or "")[:DOC_TEXT_CAP]
    except Exception as exc:  # unreadable file still gets stored
        logger.warning("rec_sourced_doc_text_failed sourced_id=%s error=%s", sp.id, exc)
        text = ""
    doc = SourcedDocument(
        agency_id=sp.agency_id,
        sourced_id=sp.id,
        kind=kind,
        storage_uri=uri,
        original_filename=file.filename,
        content_type=file.content_type,
        size_bytes=len(file.content),
        sha256=file.sha256,
        extracted_text=text or None,
    )
    db.add(doc)
    db.flush()
    db.refresh(sp)
    if kind == "cv":
        _apply_score(sp, db.get(Role, sp.role_id) if sp.role_id else None)
    db.commit()
    db.refresh(doc)
    return doc


def get_document(db: Session, sp: SourcedProfile, document_id: int) -> SourcedDocument | None:
    doc = db.get(SourcedDocument, document_id)
    if doc is None or doc.sourced_id != sp.id or doc.agency_id != sp.agency_id:
        return None
    return doc


def read_document(doc: SourcedDocument) -> bytes:
    return get_storage_service().download_bytes(doc.storage_uri)


def delete_document(db: Session, sp: SourcedProfile, doc: SourcedDocument) -> None:
    documents_service._strict_delete(get_storage_service(), doc.storage_uri)
    was_cv = doc.kind == "cv"
    db.delete(doc)
    db.flush()
    db.refresh(sp)
    if was_cv:
        _apply_score(sp, db.get(Role, sp.role_id) if sp.role_id else None)
    db.commit()


def delete_sourced(db: Session, sp: SourcedProfile) -> None:
    """Hard delete (GDPR). Removes stored files first; raises if any fail."""
    storage = get_storage_service()
    failed = 0
    for d in list(sp.documents):
        try:
            documents_service._strict_delete(storage, d.storage_uri)
        except Exception as exc:
            logger.error("rec_sourced_doc_delete_failed doc_id=%s error=%s", d.id, exc)
            failed += 1
    if failed:
        raise documents_service.DocumentDeleteError(f"{failed} stored file(s) could not be deleted")
    db.delete(sp)
    db.commit()


# ── promotion ───────────────────────────────────────────────────────────────

@dataclass
class PromoteResult:
    sourced: SourcedProfile
    candidate_id: int
    created: bool
    application_id: int | None
    documents_moved: int


def _merge_cv(db: Session, profile: CandidateProfile, cv: SourcedDocument) -> None:
    """Fold a parsed CV into the pool candidate without overwriting LinkedIn data."""
    try:
        content = read_document(cv)
        parsed = parse_cv(content, cv.original_filename)
    except Exception as exc:
        logger.warning("rec_sourced_cv_parse_failed doc_id=%s error=%s", cv.id, exc)
        return
    profile.email = profile.email or parsed.email
    profile.phone = profile.phone or parsed.phone
    profile.location = profile.location or parsed.location
    if profile.years_experience is None and parsed.years_experience is not None:
        profile.years_experience = parsed.years_experience
    if parsed.raw_text:
        profile.raw_cv_text = "\n\n".join(filter(None, [profile.raw_cv_text, parsed.raw_text]))
    have = {normalize_skill(s.name) for s in profile.skills}
    for skill in parsed.skills:
        n = normalize_skill(skill)
        if n and n not in have:
            have.add(n)
            db.add(CandidateSkill(candidate_id=profile.id, name=n))
    # LinkedIn history wins; only use the CV's dated history when LinkedIn gave none.
    if not profile.experiences:
        for exp in parsed.experiences:
            db.add(
                CandidateExperience(
                    candidate_id=profile.id,
                    title=exp.title,
                    company=exp.company,
                    start_date=exp.start_date,
                    end_date=exp.end_date,
                    description=exp.description,
                )
            )
    profile.source_file = profile.source_file or cv.original_filename


def promote(db: Session, sp: SourcedProfile, role: Role | None) -> PromoteResult:
    """Copy into the pool (dedup on LinkedIn URL), merge the CV, move the
    documents, optionally attach to `role`'s pipeline. Idempotent: promoting
    again only adds the role attachment."""
    if sp.status == STATUS_PROMOTED and sp.promoted_candidate_id and role is None:
        raise SourcingError("This profile is already in the pool")

    payload = _payload_of(sp)
    captured = capture_linkedin_profile(db, sp.agency_id, payload, role=role)
    profile = db.get(CandidateProfile, captured.candidate_id)

    moved = 0
    if sp.documents:
        cvs = [d for d in sp.documents if d.kind == "cv"]
        if cvs:
            _merge_cv(db, profile, cvs[-1])
        extra = "\n".join(filter(None, [sp.page_text]))
        if extra and extra not in (profile.raw_cv_text or ""):
            profile.raw_cv_text = "\n\n".join(filter(None, [profile.raw_cv_text, extra]))
        for d in list(sp.documents):
            db.add(
                CandidateDocument(
                    agency_id=sp.agency_id,
                    candidate_id=profile.id,
                    application_id=captured.application_id,
                    kind=d.kind,
                    storage_uri=d.storage_uri,
                    original_filename=d.original_filename,
                    content_type=d.content_type,
                    size_bytes=d.size_bytes,
                    sha256=d.sha256,
                    uploaded_via="sourced",
                )
            )
            # The pool candidate owns the stored object now; drop the
            # sourced pointer without deleting the bytes.
            db.delete(d)
            moved += 1
    elif sp.page_text and sp.page_text not in (profile.raw_cv_text or ""):
        profile.raw_cv_text = "\n\n".join(filter(None, [profile.raw_cv_text, sp.page_text]))

    db.flush()
    db.refresh(profile)
    profile.embedding = embed_candidate(profile)

    sp.status = STATUS_PROMOTED
    sp.promoted_candidate_id = profile.id
    sp.promoted_at = sp.promoted_at or datetime.utcnow()
    if role is not None:
        sp.role_id = role.id
    db.commit()
    db.refresh(sp)
    return PromoteResult(
        sourced=sp,
        candidate_id=profile.id,
        created=captured.created,
        application_id=captured.application_id,
        documents_moved=moved,
    )

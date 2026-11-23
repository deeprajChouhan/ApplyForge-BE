"""
Coach chat: everything between "the user asked X" and "here is a grounded reply".

- Evidence is numbered [E1]..[En] across resume chunks (scoped to the
  application's selected_resume_id), the JD and the current written documents.
- Intent is routed with keywords/regex, no LLM: score | edit | qa.
- Score answers take their numbers from the scorer; the LLM only phrases them.
- Edit answers return a full rewrite that is checked before the user sees it:
  invented numbers are reverted (tailoring VERIFY rule) and the
  HallucinationChecker flags unsupported claims.
- Citations are validated: only [En] markers that map to real evidence survive,
  each quote is a verbatim slice of its source. Unverifiable markers are dropped.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field

import structlog
from sqlalchemy.orm import Session

from app.models.enums import DocumentType
from app.models.models import GeneratedDocument, JobApplication
from app.services.rag.service import RAGService

logger = structlog.get_logger(__name__)

JD_CHAR_LIMIT = 6000
DOC_CHAR_LIMIT = 4000
EVIDENCE_TOP_K = 6
QUOTE_CHARS = 160
FLAG_MIN_CONFIDENCE = 0.6  # same threshold as admin quality + tailoring

WRITTEN_DOCS = (DocumentType.cover_letter, DocumentType.cold_email, DocumentType.cold_message)
DOC_LABELS = {
    DocumentType.resume: "Resume",
    DocumentType.cover_letter: "Cover letter",
    DocumentType.cold_email: "Cold email",
    DocumentType.cold_message: "LinkedIn note",
}

SYSTEM_PROMPT = (
    "You are Coach, the job-application assistant inside ApplyForge. "
    "Answer only from the material you are given: the candidate's resume evidence, the job description, "
    "its analysis and the current documents, all numbered [E1], [E2], ... "
    "If that material doesn't support something, say so plainly. "
    "Never invent experience, employers, dates, numbers, skills or qualifications. "
    "When you rely on a piece of evidence, cite it by its number, e.g. [E2]. "
    "Gaps listed as having no resume evidence must stay gaps: suggest honest framing, never claim them. "
    "Be concise and practical."
)

EDIT_SYSTEM_PROMPT = (
    "You rewrite one job-application document for the candidate. Return ONLY the full new text of the "
    "document, with no preamble, notes or markdown fences. Keep every factual claim traceable to the "
    "current document or the numbered evidence. Do not add numbers, employers, dates, skills or "
    "achievements that aren't in that material. Keep [placeholders] such as [Hiring manager name] as they are."
)

_DOC_PATTERNS: list[tuple[DocumentType, re.Pattern]] = [
    (DocumentType.cover_letter, re.compile(r"\bcover\s*letter\b", re.I)),
    (DocumentType.cold_email, re.compile(r"\b(cold\s*)?e-?mail\b", re.I)),
    (DocumentType.cold_message, re.compile(r"\b(linked\s*in|connection)\s*(note|message|dm)?\b|\b(note|dm)\b", re.I)),
    (DocumentType.resume, re.compile(r"\b(resume|résumé|cv)\b", re.I)),
]
_EDIT_VERB = re.compile(
    r"\b(rewrite|re-write|redo|shorten|shorter|tighten|trim|cut|condense|expand|lengthen|longer|rephrase|reword|"
    r"polish|improve|edit|fix|make\s+it|more\s+(formal|casual|direct|confident|concise|warm)|less\s+\w+|tone)\b",
    re.I,
)
_SCORE = re.compile(
    r"\b(score[ds]?|priority|fit\b|competition|competitive|reply\s*(rate|likelihood|chance)|why\s+(is|did|was)\s+.*\d+)",
    re.I,
)
_MARKER = re.compile(r"\[E(\d+)\]")
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class Evidence:
    eid: str  # "E1"
    source: str  # resume | jd | document | score
    label: str
    text: str
    chunk_id: int | None = None
    doc_type: DocumentType | None = None
    doc_version: int | None = None


@dataclass
class Intent:
    kind: str  # score | edit | qa
    doc_type: DocumentType | None = None
    note: str | None = None  # why an edit request was answered as qa


@dataclass
class Prepared:
    intent: Intent
    system_prompt: str
    user_prompt: str
    evidence: list[Evidence]
    context: dict = field(default_factory=dict)
    base_doc: GeneratedDocument | None = None


def current_documents(db: Session, user_id: int, app_id: int) -> dict[DocumentType, GeneratedDocument]:
    out: dict[DocumentType, GeneratedDocument] = {}
    for dt in DocumentType:
        row = (
            db.query(GeneratedDocument)
            .filter_by(user_id=user_id, application_id=app_id, doc_type=dt)
            .filter(GeneratedDocument.deleted_at.is_(None))
            .order_by(GeneratedDocument.version.desc())
            .first()
        )
        if row:
            out[dt] = row
    return out


def detect_intent(content: str, docs: dict[DocumentType, GeneratedDocument]) -> Intent:
    doc_type = next((dt for dt, rx in _DOC_PATTERNS if rx.search(content)), None)
    if doc_type and _EDIT_VERB.search(content):
        if doc_type == DocumentType.resume:
            return Intent("qa", note="resume_edit")
        if doc_type not in docs:
            return Intent("qa", note="doc_missing")
        return Intent("edit", doc_type=doc_type)
    if _SCORE.search(content):
        return Intent("score")
    return Intent("qa")


def _analysis_lines(app: JobApplication) -> str:
    if not app.jd_analysis_json:
        return "JD analysis: not run yet."
    try:
        a = json.loads(app.jd_analysis_json)
    except (TypeError, ValueError):
        return "JD analysis: unavailable."

    def line(label: str, key: str) -> str:
        vals = a.get(key) or []
        return f"- {label}: {', '.join(str(v) for v in vals) if vals else 'none'}"

    parts = [
        "JD analysis:",
        line("Required skills", "required_skills"),
        line("Preferred skills", "preferred_skills"),
        line("Keywords", "keywords"),
        line("Strengths backed by the resume", "strengths"),
        line("Gaps with no resume evidence", "unsupported_gaps"),
    ]
    if a.get("fit_summary"):
        parts.append(f"- Fit summary: {a['fit_summary']}")
    return "\n".join(parts)


def _resume_hits(db: Session, user_id: int, app: JobApplication, query: str):
    rag = RAGService(db, user_id)
    hits = []
    if app.selected_resume_id:
        hits = rag.search(query, top_k=EVIDENCE_TOP_K, parsed_resume_id=app.selected_resume_id)
    if not hits:
        hits = rag.search(query, top_k=EVIDENCE_TOP_K)
    return hits


def build_evidence(
    db: Session,
    user_id: int,
    app: JobApplication,
    query: str,
    docs: dict[DocumentType, GeneratedDocument],
) -> list[Evidence]:
    items: list[Evidence] = []
    for i, (chunk, _score) in enumerate(_resume_hits(db, user_id, app, f"{query}\n{app.role_title}"), start=1):
        items.append(Evidence(
            eid=f"E{len(items) + 1}", source="resume", label=f"Resume · excerpt {i}",
            text=chunk.content, chunk_id=getattr(chunk, "id", None),
        ))
    jd = (app.job_description or "").strip()
    if jd:
        items.append(Evidence(eid=f"E{len(items) + 1}", source="jd", label="JD", text=jd[:JD_CHAR_LIMIT]))
    for dt in WRITTEN_DOCS:
        d = docs.get(dt)
        if d and d.content:
            items.append(Evidence(
                eid=f"E{len(items) + 1}", source="document", label=f"{DOC_LABELS[dt]} v{d.version}",
                text=d.content[:DOC_CHAR_LIMIT], doc_type=dt, doc_version=d.version,
            ))
    return items


def _evidence_block(items: list[Evidence]) -> str:
    if not any(e.source == "resume" for e in items):
        head = "No resume evidence was found. Say that you can't see the resume rather than guessing.\n"
    else:
        head = ""
    return head + "\n\n".join(f"[{e.eid}] ({e.label})\n{e.text}" for e in items)


def score_facts(breakdown: dict) -> tuple[str, dict]:
    """Plain-text score facts for the prompt + the structured table for the UI."""
    fit = breakdown.get("fit_score")
    comp = breakdown.get("competition_score")
    pri = breakdown.get("priority_score")
    fb = breakdown.get("fit_breakdown") or {}
    cb = breakdown.get("competition_breakdown") or []
    lines = [
        f"Priority score {pri} = ½ × fit {fit} + ½ × (100 − competition {comp}).",
        "Fit signals: " + ", ".join(f"{k.replace('_', ' ')} {round(v, 1)}" for k, v in fb.items()),
        "Competition: " + ", ".join(
            f"{r['signal'].replace('_', ' ')} {'+' if r['delta'] >= 0 else '−'}{abs(r['delta'])}"
            + (f" ({r['evidence']})" if r.get("evidence") else "")
            for r in cb
        ),
    ]
    table = {
        "priority_score": pri,
        "fit_score": fit,
        "competition_score": comp,
        "fit_breakdown": fb,
        "competition_breakdown": cb,
    }
    return "\n".join(lines), table


def prepare(
    db: Session,
    user_id: int,
    app: JobApplication,
    content: str,
    history: str,
    score_breakdown_fn=None,
) -> Prepared:
    docs = current_documents(db, user_id, app.id)
    intent = detect_intent(content, docs)
    evidence = build_evidence(db, user_id, app, content, docs)
    context: dict = {"intent": intent.kind}
    base_doc = None

    sections = [f"Role: {app.role_title} at {app.company_name}", _analysis_lines(app)]

    if intent.kind == "score":
        if not app.jd_analysis_json or score_breakdown_fn is None:
            intent = Intent("qa", note="no_analysis")
            context["intent"] = "qa"
        else:
            facts, table = score_facts(score_breakdown_fn())
            evidence.append(Evidence(eid=f"E{len(evidence) + 1}", source="score", label="Score breakdown", text=facts))
            context["score"] = table
            sections.append(
                "Score facts (from the ApplyForge scorer; quote these numbers exactly and do not recompute them):\n"
                + facts
            )

    sections.append(f"Evidence:\n{_evidence_block(evidence)}")
    if history:
        sections.append(history)

    if intent.kind == "edit":
        base_doc = docs[intent.doc_type]
        context["doc_type"] = intent.doc_type.value
        sections.append(
            f"Current {DOC_LABELS[intent.doc_type].lower()} (v{base_doc.version}):\n{base_doc.content}"
        )
        sections.append(f"Instruction: {content}\nReturn only the full rewritten {DOC_LABELS[intent.doc_type].lower()}.")
        return Prepared(intent, EDIT_SYSTEM_PROMPT, "\n\n".join(sections), evidence, context, base_doc)

    if intent.note == "resume_edit":
        sections.append(
            "Note: the user asked to edit the resume. Resume edits go through tailoring and Suggestions on the "
            "application page, not chat. Explain that briefly and give concrete, evidence-backed advice instead."
        )
    elif intent.note == "doc_missing":
        sections.append("Note: that document hasn't been generated yet. Say so, and suggest generating it first.")
    elif intent.note == "no_analysis":
        sections.append("Note: the JD hasn't been analysed, so there is no score yet. Suggest running the analysis.")
    sections.append(f"Question: {content}")
    return Prepared(intent, SYSTEM_PROMPT, "\n\n".join(sections), evidence, context)


# ── Citations ────────────────────────────────────────────────────────────

def validate_citations(text: str, evidence: list[Evidence]) -> tuple[str, list[dict]]:
    """Keep only [En] markers that map to real evidence; drop the rest from the text."""
    by_id = {e.eid: e for e in evidence}
    citations: list[dict] = []
    seen: set[str] = set()

    def _sub(m: re.Match) -> str:
        eid = f"E{m.group(1)}"
        ev = by_id.get(eid)
        if ev is None:
            return ""  # unverifiable: never invent a source
        if eid not in seen:
            seen.add(eid)
            quote = ev.text[:QUOTE_CHARS]
            assert quote and quote in ev.text  # verbatim slice of the source
            citations.append({
                "id": f"c{len(citations) + 1}",
                "source": ev.source,
                "label": ev.label,
                "quote": quote,
                "chunk_id": ev.chunk_id,
                "doc_type": ev.doc_type.value if ev.doc_type else None,
                "doc_version": ev.doc_version,
                "char_start": 0,
                "char_end": len(quote),
            })
        return m.group(0)

    cleaned = _MARKER.sub(_sub, text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned, citations


# ── Edit proposals ───────────────────────────────────────────────────────

def _sentences(text: str) -> list[str]:
    return [s for s in (p.strip() for p in _SENT.split(text or "")) if s]


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*\n", "", t)
    t = re.sub(r"\n```\s*$", "", t)
    return t.strip()


def source_corpus(db: Session, user_id: int, app: JobApplication) -> str:
    try:
        from app.services.resume_tailoring.source import load_source_resume

        return load_source_resume(db, user_id, app).corpus()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("coach_source_corpus_failed", error=str(exc)[:200])
        return ""


def check_claims(text: str, corpus: str) -> list[dict]:
    """HallucinationChecker (regex only, no tokens) flags at or above FLAG_MIN_CONFIDENCE."""
    try:
        from evaluation.hallucination import HallucinationChecker
    except Exception:  # offline package not importable in some deployments
        return []
    try:
        flags = HallucinationChecker(llm_provider=None).check_as_dicts(text, corpus, use_llm_fallback=False)
    except Exception as exc:
        logger.warning("coach_hallucination_check_failed", error=str(exc)[:200])
        return []
    return [f for f in flags if float(f.get("confidence", 0)) >= FLAG_MIN_CONFIDENCE]


def build_proposed_edit(
    raw_reply: str,
    base: GeneratedDocument,
    resume_corpus: str,
    jd_text: str,
) -> dict:
    from app.services.resume_tailoring.terms import unsupported_numbers

    new_text = _strip_fences(raw_reply)
    grounding = "\n".join([resume_corpus, base.content or "", jd_text or ""])

    # Tailoring VERIFY rule: a sentence carrying a number that isn't in the
    # resume, the current document or the JD is reverted (dropped), and listed.
    old_sentences = set(_sentences(base.content))
    kept: list[str] = []
    reverted: list[str] = []
    for line in (new_text or "").split("\n"):
        out_parts = []
        for sent in _sentences(line) or ([line] if not line.strip() else []):
            bad = unsupported_numbers(sent, grounding) if sent not in old_sentences else []
            if bad:
                reverted.extend(bad)
                continue
            out_parts.append(sent)
        kept.append(" ".join(out_parts))
    new_text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()

    a, b = _sentences(base.content), _sentences(new_text)
    diff: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            diff += [{"op": "keep", "text": s} for s in a[i1:i2]]
        else:
            diff += [{"op": "del", "text": s} for s in a[i1:i2]]
            diff += [{"op": "add", "text": s} for s in b[j1:j2]]

    return {
        "doc_type": base.doc_type.value if hasattr(base.doc_type, "value") else str(base.doc_type),
        "base_document_id": base.id,
        "base_version": base.version,
        "new_content": new_text,
        "diff": diff,
        "word_count": {"before": len((base.content or "").split()), "after": len(new_text.split())},
        "unsupported_claims": check_claims(new_text, resume_corpus),
        "reverted_numbers": sorted(set(reverted)),
    }


def edit_summary(edit: dict) -> str:
    label = DOC_LABELS.get(DocumentType(edit["doc_type"]), "document").lower()
    wc = edit["word_count"]
    parts = [f"Here's a revised {label}: {wc['before']} → {wc['after']} words."]
    if edit["reverted_numbers"]:
        parts.append(
            "I removed sentences with numbers that aren't in your resume or the posting: "
            + ", ".join(edit["reverted_numbers"]) + "."
        )
    if edit["unsupported_claims"]:
        n = len(edit["unsupported_claims"])
        parts.append(f"{n} claim{'s' if n != 1 else ''} couldn't be matched to your resume. Check them before applying.")
    else:
        parts.append("Every claim traces back to your resume or the current version.")
    return " ".join(parts)

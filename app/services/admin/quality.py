"""Generation-quality oversight for the operator console.

Two parts:

* :func:`quality_summary` — read-only metrics: hallucination-flag rate from
  ``application_evaluations``, resume-parse confidence, generation volume, and
  recorded application outcomes.
* :func:`run_spot_check` — an operator-triggered audit that runs the offline
  regex :class:`HallucinationChecker` (no LLM, no token cost) over a sample of
  recent generated documents, grounded against the user's own resume + profile,
  and upserts the results into ``application_evaluations``.

The spot-check never touches the generation flow; it only reads generated
documents and writes evaluation rows. Flags are heuristic — they are leads for
human review, not verdicts.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.evaluation_models import ApplicationEvaluation
from app.models.models import (
    Certification,
    Education,
    GeneratedDocument,
    ParsedResumeData,
    Project,
    Skill,
    User,
    UserProfile,
    WorkExperience,
)

FLAG_MIN_CONFIDENCE = 0.6     # a doc counts as "flagged" if any flag is at least this confident
LOW_PARSE_CONFIDENCE = 0.6
MAX_SPOT_CHECK = 200


def _parse_flags(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


def quality_summary(db: Session, days: int = 30) -> dict[str, Any]:
    since = datetime.utcnow() - timedelta(days=days)

    evals = (
        db.query(ApplicationEvaluation, User.email)
        .outerjoin(User, User.id == ApplicationEvaluation.user_id)
        .filter(ApplicationEvaluation.evaluated_at >= since, ApplicationEvaluation.hallucination_flags_json.isnot(None))
        .order_by(ApplicationEvaluation.evaluated_at.desc())
        .all()
    )
    by_doc: dict[str, dict[str, int]] = defaultdict(lambda: {"evaluated": 0, "flagged": 0})
    by_claim: Counter[str] = Counter()
    flagged_rows = []
    total_flags = 0
    for ev, email in evals:
        flags = [f for f in _parse_flags(ev.hallucination_flags_json) if float(f.get("confidence", 0)) >= FLAG_MIN_CONFIDENCE]
        by_doc[ev.doc_type]["evaluated"] += 1
        total_flags += len(flags)
        for f in flags:
            by_claim[str(f.get("claim_type", "other"))] += 1
        if flags:
            by_doc[ev.doc_type]["flagged"] += 1
            flagged_rows.append(
                {
                    "application_id": ev.application_id,
                    "user_id": ev.user_id,
                    "email": email,
                    "doc_type": ev.doc_type,
                    "flag_count": len(flags),
                    "evaluated_at": ev.evaluated_at,
                    "flags": [
                        {k: f.get(k) for k in ("claim", "claim_type", "reason", "confidence")} for f in flags[:5]
                    ],
                }
            )

    evaluated = len(evals)
    flagged = len(flagged_rows)

    generated = (
        db.query(func.count(GeneratedDocument.id))
        .filter(GeneratedDocument.created_at >= since, GeneratedDocument.deleted_at.is_(None))
        .scalar()
        or 0
    )

    parses = (
        db.query(ParsedResumeData.confidence_score)
        .filter(ParsedResumeData.created_at >= since, ParsedResumeData.deleted_at.is_(None))
        .all()
    )
    confs = [float(c or 0) for (c,) in parses]

    outcomes = dict(
        db.query(ApplicationEvaluation.outcome, func.count(ApplicationEvaluation.id))
        .filter(ApplicationEvaluation.outcome.isnot(None))
        .group_by(ApplicationEvaluation.outcome)
        .all()
    )

    last_check = (
        db.query(func.max(ApplicationEvaluation.evaluated_at))
        .filter(ApplicationEvaluation.hallucination_flags_json.isnot(None))
        .scalar()
    )

    return {
        "window_days": days,
        "hallucination": {
            "evaluated_docs": evaluated,
            "flagged_docs": flagged,
            "flag_rate": round(flagged / evaluated, 4) if evaluated else None,
            "avg_flags_per_doc": round(total_flags / evaluated, 2) if evaluated else None,
            "min_confidence": FLAG_MIN_CONFIDENCE,
            "by_doc_type": [{"doc_type": k, **v} for k, v in sorted(by_doc.items())],
            "by_claim_type": [{"claim_type": k, "count": v} for k, v in by_claim.most_common()],
            "recent_flagged": flagged_rows[:15],
            "last_checked_at": last_check,
        },
        "generation": {
            "documents_generated": generated,
            "evaluation_coverage": round(evaluated / generated, 4) if generated else None,
        },
        "parsing": {
            "parses": len(confs),
            "avg_confidence": round(sum(confs) / len(confs), 3) if confs else None,
            "low_confidence": sum(1 for c in confs if c < LOW_PARSE_CONFIDENCE),
            "threshold": LOW_PARSE_CONFIDENCE,
        },
        "outcomes": {k: v for k, v in outcomes.items() if k},
    }


def _grounding_text(db: Session, user_id: int) -> str:
    """Everything the generator is allowed to draw on: parsed resume + profile."""
    parts: list[str] = []
    parsed = (
        db.query(ParsedResumeData.raw_text)
        .filter(ParsedResumeData.user_id == user_id, ParsedResumeData.deleted_at.is_(None))
        .order_by(ParsedResumeData.id.desc())
        .first()
    )
    if parsed and parsed[0]:
        parts.append(parsed[0])
    prof = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if prof:
        parts += [p for p in (prof.headline, prof.summary, prof.current_role) if p]
    for w in db.query(WorkExperience).filter(WorkExperience.user_id == user_id).all():
        parts.append(" ".join(x for x in (w.role, "at", w.company, w.description or "") if x))
    for e in db.query(Education).filter(Education.user_id == user_id).all():
        parts.append(" ".join(x for x in (e.degree or "", e.field_of_study or "", e.institution) if x))
    for p in db.query(Project).filter(Project.user_id == user_id).all():
        parts.append(" ".join(x for x in (p.name, p.description or "", p.technologies or "") if x))
    parts += [s.name for s in db.query(Skill).filter(Skill.user_id == user_id).all()]
    parts += [" ".join(x for x in (c.name, c.issuer or "") if x) for c in db.query(Certification).filter(Certification.user_id == user_id).all()]
    return "\n".join(parts)


def run_spot_check(db: Session, sample_size: int = 50, days: int = 30) -> dict[str, Any]:
    """Check the latest version of up to ``sample_size`` recent generated docs."""
    from evaluation.hallucination import HallucinationChecker  # offline module; regex-only here

    sample_size = max(1, min(sample_size, MAX_SPOT_CHECK))
    since = datetime.utcnow() - timedelta(days=days)

    latest = (
        db.query(func.max(GeneratedDocument.id))
        .filter(GeneratedDocument.created_at >= since, GeneratedDocument.deleted_at.is_(None))
        .group_by(GeneratedDocument.application_id, GeneratedDocument.doc_type)
        .subquery()
    )
    docs = (
        db.query(GeneratedDocument)
        .filter(GeneratedDocument.id.in_(db.query(latest)))
        .order_by(GeneratedDocument.created_at.desc())
        .limit(sample_size)
        .all()
    )

    checker = HallucinationChecker(llm_provider=None)
    grounding_cache: dict[int, str] = {}
    checked = flagged = skipped = 0
    for doc in docs:
        if doc.user_id not in grounding_cache:
            grounding_cache[doc.user_id] = _grounding_text(db, doc.user_id)
        source = grounding_cache[doc.user_id]
        if not source.strip() or not (doc.content or "").strip():
            skipped += 1
            continue
        flags = checker.check_as_dicts(doc.content, source, use_llm_fallback=False)
        strong = [f for f in flags if float(f.get("confidence", 0)) >= FLAG_MIN_CONFIDENCE]
        doc_type = getattr(doc.doc_type, "value", doc.doc_type)
        row = (
            db.query(ApplicationEvaluation)
            .filter_by(application_id=doc.application_id, doc_type=doc_type)
            .first()
        )
        if row is None:
            row = ApplicationEvaluation(application_id=doc.application_id, user_id=doc.user_id, doc_type=doc_type)
            db.add(row)
        row.hallucination_flags_json = json.dumps(flags, default=str)
        row.hallucination_count = len(strong)
        row.evaluated_at = datetime.utcnow()
        checked += 1
        flagged += bool(strong)
    db.commit()
    return {
        "checked": checked,
        "flagged": flagged,
        "skipped_no_grounding": skipped,
        "flag_rate": round(flagged / checked, 4) if checked else None,
        "window_days": days,
    }

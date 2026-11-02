"""
Keyword densification for role / job-listing creation.

Recruiters usually know 3–4 keywords for a role. Thin keyword sets hurt both
matching (fewer skills to score against) and sourcing (narrow boolean
strings). This service expands them from three sources, each labelled so the
recruiter can see WHY a keyword is suggested and accept/reject it — nothing
is added to the role automatically (recruiter-in-the-loop):

  1. pool    — co-occurrence in the agency's OWN candidate pool: candidates
               who have the given skills also commonly list X (with lift).
               Pure data, no LLM, scoped to agency_id.
  2. jd      — skills found in the role title / description text itself.
  3. ai      — LLM suggestions grounded in the title + description + given
               keywords; each must cite which input implies it. Fail-soft:
               without an LLM key, a deterministic adjacency fallback runs.

Also returns synonyms/aliases per keyword (for boolean search strings) and a
ready-to-paste boolean search string.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.recruiter.models import CandidateProfile, CandidateSkill
from app.recruiter.services import ai_support
from app.recruiter.services.skills import (
    _SKILL_ALIASES,
    extract_skills,
    normalize_skill,
    related_skills,
)

MAX_SUGGESTIONS = 20


def _aliases(skill: str) -> list[str]:
    canon = normalize_skill(skill)
    return [a for a in _SKILL_ALIASES.get(canon, []) if len(a) > 2][:3]


def _pool_cooccurrence(
    db: Session, agency_id: int, seeds: set[str], exclude: set[str]
) -> list[dict[str, Any]]:
    """Skills that over-index among pool candidates holding any seed skill."""
    rows = (
        db.query(CandidateSkill.candidate_id, CandidateSkill.name)
        .join(CandidateProfile, CandidateProfile.id == CandidateSkill.candidate_id)
        .filter(CandidateProfile.agency_id == agency_id)
        .all()
    )
    by_cand: dict[int, set[str]] = {}
    for cid, name in rows:
        if name:
            by_cand.setdefault(cid, set()).add(normalize_skill(name))
    total = len(by_cand)
    if total < 3 or not seeds:
        return []
    overall = Counter(s for skills in by_cand.values() for s in skills)
    matched = [skills for skills in by_cand.values() if skills & seeds]
    if len(matched) < 2:
        return []
    co = Counter(s for skills in matched for s in skills)
    out = []
    for skill, n in co.most_common(60):
        if skill in exclude or n < 2:
            continue
        lift = (n / len(matched)) / (overall[skill] / total)
        if lift < 1.15:
            continue
        out.append({
            "skill": skill,
            "source": "pool",
            "reason": f"{n} of {len(matched)} pool candidates with your keywords also list it",
            "confidence": round(min(0.95, 0.4 + 0.5 * (n / len(matched))), 2),
            "pool_count": overall[skill],
        })
    return out[:10]


def _ai_suggestions(
    title: str, seniority: str | None, description: str | None,
    required: list[str], preferred: list[str],
) -> list[dict[str, Any]] | None:
    if not ai_support.llm_enabled():
        return None
    system = (
        "You help a recruitment consultant expand a thin keyword list for a role so "
        "candidate matching and sourcing are better. Suggest ONLY skills, tools, "
        "certifications or domain terms that are strongly implied by the role title, "
        "seniority, description or the given keywords. Do not suggest generic soft "
        "skills unless the description asks for them. For each suggestion say which "
        "input implies it. Classify as 'required' only if nearly every person in this "
        "role would need it, otherwise 'preferred'. Return STRICT JSON."
    )
    schema = (
        'Return JSON: {"suggestions": [{"skill": string, "kind": "required"|"preferred", '
        '"category": "synonym"|"adjacent"|"tool"|"domain"|"certification"|"soft", '
        '"implied_by": string, "confidence": number 0-1}]}  (max 15 items, skill names '
        "short, lowercase, as written on CVs)"
    )
    ctx = [
        f"Title: {title}",
        f"Seniority: {seniority or 'unspecified'}",
        f"Required keywords given: {', '.join(required) or 'none'}",
        f"Preferred keywords given: {', '.join(preferred) or 'none'}",
    ]
    if description:
        ctx.append(f"Description:\n{description[:4000]}")
    data = ai_support.generate_json(system, schema + "\n\n" + "\n".join(ctx))
    if not isinstance(data, dict) or not isinstance(data.get("suggestions"), list):
        return None
    out = []
    for item in data["suggestions"][:15]:
        if not isinstance(item, dict) or not isinstance(item.get("skill"), str):
            continue
        skill = normalize_skill(item["skill"])[:80]
        if not skill:
            continue
        try:
            conf = float(item.get("confidence", 0.6))
        except (TypeError, ValueError):
            conf = 0.6
        out.append({
            "skill": skill,
            "source": "ai",
            "kind": "required" if item.get("kind") == "required" else "preferred",
            "category": str(item.get("category") or "adjacent")[:20],
            "reason": f"Implied by {str(item.get('implied_by') or 'the role')[:120]}",
            "confidence": round(max(0.1, min(0.95, conf)), 2),
        })
    return out


def densify_keywords(
    db: Session,
    agency_id: int,
    *,
    title: str,
    seniority: str | None = None,
    description: str | None = None,
    required_skills: list[str] | None = None,
    preferred_skills: list[str] | None = None,
) -> dict[str, Any]:
    required = list(dict.fromkeys(normalize_skill(s) for s in (required_skills or []) if s.strip()))
    preferred = [
        s for s in dict.fromkeys(normalize_skill(s) for s in (preferred_skills or []) if s.strip())
        if s not in required
    ]
    have = set(required) | set(preferred)
    suggestions: dict[str, dict[str, Any]] = {}

    def add(item: dict[str, Any]) -> None:
        s = item["skill"]
        if s in have:
            return
        cur = suggestions.get(s)
        if cur is None:
            item.setdefault("kind", "preferred")
            item.setdefault("category", "adjacent")
            item["sources"] = [item.pop("source")]
            suggestions[s] = item
        else:
            src = item.get("source")
            if src and src not in cur["sources"]:
                cur["sources"].append(src)
                # Agreement across independent sources raises confidence.
                cur["confidence"] = round(min(0.97, cur["confidence"] + 0.15), 2)
            if item.get("kind") == "required":
                cur["kind"] = "required"
            if item.get("pool_count") is not None:
                cur["pool_count"] = item["pool_count"]

    # 2. JD text — strongest evidence: the client literally wrote it.
    for s in extract_skills(f"{title} {description or ''}"):
        add({"skill": s, "source": "jd", "kind": "required", "category": "domain",
             "reason": "Mentioned in the role title/description", "confidence": 0.85})

    # 3. AI (or deterministic fallback)
    ai = _ai_suggestions(title, seniority, description, required, preferred)
    used_llm = ai is not None
    if ai:
        for item in ai:
            add(item)
    else:
        for seed in required + preferred:
            for rel in sorted(related_skills(seed)):
                add({"skill": rel, "source": "ai", "kind": "preferred", "category": "adjacent",
                     "reason": f"Adjacent to {seed}", "confidence": 0.55})

    # 1. Pool co-occurrence (agency-scoped)
    for item in _pool_cooccurrence(db, agency_id, set(required) | set(preferred), have):
        add(item)

    # Pool counts for everything we suggest (helps the recruiter see impact).
    pool_skills = Counter(
        normalize_skill(n)
        for (n,) in db.query(CandidateSkill.name)
        .join(CandidateProfile, CandidateProfile.id == CandidateSkill.candidate_id)
        .filter(CandidateProfile.agency_id == agency_id)
        .all()
        if n
    )
    ranked = sorted(
        suggestions.values(),
        key=lambda x: (-x["confidence"], -len(x["sources"]), x["skill"]),
    )[:MAX_SUGGESTIONS]
    for item in ranked:
        item["pool_count"] = pool_skills.get(item["skill"], 0)
        item["aliases"] = _aliases(item["skill"])

    synonyms = {s: _aliases(s) for s in required + preferred if _aliases(s)}

    def _term(s: str) -> str:
        variants = [s] + synonyms.get(s, [])
        quoted = [f'"{v}"' if " " in v else v for v in variants]
        return quoted[0] if len(quoted) == 1 else "(" + " OR ".join(quoted) + ")"

    boolean = ""
    if required:
        boolean = " AND ".join(_term(s) for s in required[:6])
        nice = [s["skill"] for s in ranked if s["kind"] == "preferred"][:5] + preferred[:3]
        if nice:
            boolean += " AND (" + " OR ".join(f'"{n}"' if " " in n else n for n in nice) + ")"

    thin = len(required) + len(preferred) < 6
    return {
        "suggestions": ranked,
        "synonyms": synonyms,
        "boolean_search": boolean or None,
        "keyword_count": len(required) + len(preferred),
        "is_thin": thin,
        "advice": (
            "Fewer than 6 keywords — matching and sourcing will be narrow. "
            "Accept the suggestions that genuinely apply to this role."
            if thin else None
        ),
        "used_llm": used_llm,
    }

"""Pure-Python heuristic scoring for auto-apply job matching.

No LLM calls here — this is intentionally cheap/synchronous heuristic
scoring so it can run for every candidate job on every tick. LLM-based
scoring (semantic match, resume-aware) is planned for Phase 4.
"""
from __future__ import annotations

from typing import Any, Dict, List


def _safe_list(value: Any) -> List[str]:
    """Best-effort coercion of a JSON column value into a list of strings."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if isinstance(value, str):
        return [value]
    return []


def _lower(value: Any) -> str:
    return str(value).lower() if value else ""


def score_job_for_user(user: Any, job: Any, settings: Any) -> Dict[str, Any]:
    """Compute a transparent 0..100 match score for `job` against `settings`.

    Returns:
        {
            "score": int,
            "band": "top" | "strong" | "good" | "weak",
            "reasons": [{"kind": str, "detail": str, "delta": int}, ...],
        }
    """
    reasons: List[Dict[str, Any]] = []
    # Start LOW so a job that matches nothing scores ~20 (weak) instead of 50.
    # A job that matches at least one target title climbs into good/strong.
    score = 20

    job_title = _lower(getattr(job, "title", None))
    job_description = _lower(getattr(job, "description", None))
    job_remote_mode = getattr(job, "remote_mode", None)
    job_location = _lower(getattr(job, "location", None))

    company = getattr(job, "company", None)
    company_name = getattr(company, "name", None) if company is not None else None

    target_titles = _safe_list(getattr(settings, "target_titles_json", None))
    locations = _safe_list(getattr(settings, "locations_json", None))
    excluded_companies = _safe_list(getattr(settings, "excluded_companies_json", None))
    excluded_keywords = _safe_list(getattr(settings, "excluded_keywords_json", None))
    remote_only = bool(getattr(settings, "remote_only", False))

    # Title match — big signal. Each matched title token adds up to +55.
    # If user set target_titles but none match, cap the final score at 20
    # (weak) so unrelated roles never make it through auto-apply.
    title_matched = False
    if job_title and target_titles:
        for title in target_titles:
            title_l = _lower(title)
            if not title_l:
                continue
            # Match on any target-title WORD present in the job title, not
            # just the whole phrase (so "AI Engineer" matches "Senior AI
            # Platform Engineer"). Require ALL words of the target to hit.
            tokens = [t for t in title_l.split() if len(t) > 1]
            if tokens and all(tok in job_title for tok in tokens):
                score += 55
                reasons.append(
                    {"kind": "title_match", "detail": f"title matches '{title}'", "delta": 55}
                )
                title_matched = True
                break

    # Hard filter: if target_titles is set and nothing matched, cap at weak.
    if target_titles and not title_matched:
        reasons.append(
            {
                "kind": "title_mismatch",
                "detail": "job title does not match any target title",
                "delta": 0,
            }
        )
        return {"score": min(score, 20), "band": "weak", "reasons": reasons}

    # Remote-only mismatch.
    if remote_only and job_remote_mode != "remote":
        score -= 30
        reasons.append(
            {
                "kind": "remote_only_mismatch",
                "detail": f"remote_only set but job.remote_mode={job_remote_mode!r}",
                "delta": -30,
            }
        )

    # Location preference match.
    if locations and job_location:
        for loc in locations:
            loc_l = _lower(loc)
            if loc_l and loc_l in job_location:
                score += 10
                reasons.append(
                    {"kind": "location_match", "detail": f"location contains '{loc}'", "delta": 10}
                )
                break

    # Excluded company (hard exclusion).
    if company_name and excluded_companies:
        company_name_l = _lower(company_name)
        for excluded in excluded_companies:
            if _lower(excluded) == company_name_l:
                score -= 100
                reasons.append(
                    {
                        "kind": "excluded_company",
                        "detail": f"company '{company_name}' is excluded",
                        "delta": -100,
                    }
                )
                break

    # Excluded keywords, capped at -60 total.
    if excluded_keywords:
        keyword_penalty = 0
        haystack = f"{job_title} {job_description}"
        for keyword in excluded_keywords:
            keyword_l = _lower(keyword)
            if keyword_l and keyword_l in haystack:
                if keyword_penalty >= 60:
                    break
                delta = min(20, 60 - keyword_penalty)
                keyword_penalty += delta
                reasons.append(
                    {
                        "kind": "excluded_keyword",
                        "detail": f"matched excluded keyword '{keyword}'",
                        "delta": -delta,
                    }
                )
        score -= keyword_penalty

    score = max(0, min(100, score))

    if score >= 85:
        band = "top"
    elif score >= 75:
        band = "strong"
    elif score >= 60:
        band = "good"
    else:
        band = "weak"

    return {"score": score, "band": band, "reasons": reasons}

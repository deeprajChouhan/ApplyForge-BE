"""
Priority Score Engine
=====================
Computes two independent sub-scores and composes them into a single
priority_score (0–100) with a human-readable recommendation.

Sub-scores
----------
fit_score          -- How well the candidate matches the JD.
                      Composed from 8 independent signals (see FitScorer).
competition_score  -- Estimated difficulty / selectivity of the role.

Priority formula
----------------
  priority = fit * 0.50 + (100 - competition) * 0.50

All scores are floats in [0, 100].
"""
from __future__ import annotations

import re
from datetime import date as _date
from typing import Any


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_date(val: Any) -> _date | None:
    """Coerce a datetime or date value to a plain date, handling None."""
    if val is None:
        return None
    if hasattr(val, "date"):          # datetime → date
        return val.date()
    return val                        # already a date


# ── Fit Scorer ────────────────────────────────────────────────────────────────

class FitScorer:
    """
    Multi-signal fit scorer.

    Returns ``(composite_score, breakdown_dict)`` where composite is in [0, 100]
    and breakdown maps each signal name to the points it contributed.

    Signal budget (total max = 100 pts)
    ┌────────────────────────────┬─────────┐
    │ Signal                     │ Max pts │
    ├────────────────────────────┼─────────┤
    │ Required skills coverage   │    25   │
    │ Preferred skills coverage  │    10   │
    │ Strengths / gaps ratio     │    15   │
    │ Keyword density bonus      │    10   │
    │ Title / seniority alignment│    15   │
    │ Experience depth (YoE)     │    15   │
    │ Domain / industry alignment│     7   │
    │ Education match            │     3   │
    └────────────────────────────┴─────────┘
    """

    _YOE_RE = re.compile(r'(\d+)\+?\s*(?:to\s*\d+\s*)?years?', re.IGNORECASE)

    # Ordered longest-first so multi-word phrases match before single words.
    _SENIORITY_TIERS: list[tuple[str, int]] = sorted([
        ("cto", 9), ("ceo", 9), ("coo", 9), ("cpo", 9), ("cfo", 9),
        ("vice president", 8), ("vp", 8),
        ("head of", 7), ("director", 7),
        ("principal", 6), ("architect", 6), ("distinguished", 6),
        ("staff", 5), ("lead", 5), ("manager", 5), ("engineering manager", 5),
        ("senior", 4), ("sr.", 4),
        ("mid level", 3), ("mid-level", 3), ("associate", 3), ("intermediate", 3),
        ("junior", 2), ("jr.", 2),
        ("entry level", 1), ("entry-level", 1), ("graduate", 1), ("fresher", 1),
        ("intern", 0), ("internship", 0),
    ], key=lambda x: -len(x[0]))  # longest first for greedy match

    _DOMAIN_CLUSTERS: dict[str, list[str]] = {
        "fintech":    ["fintech", "financial", "banking", "payments", "trading",
                       "investment", "wealth", "insurance", "lending", "credit",
                       "defi", "blockchain", "crypto"],
        "healthcare": ["healthcare", "medical", "clinical", "hospital", "pharma",
                       "pharmaceutical", "biotech", "patient", "ehr", "emr",
                       "telemedicine"],
        "ecommerce":  ["e-commerce", "ecommerce", "retail", "marketplace",
                       "shopify", "cart", "checkout", "fulfillment"],
        "saas":       ["saas", "b2b", "subscription", "mrr", "arr", "churn",
                       "multi-tenant", "crm", "erp"],
        "media":      ["streaming", "entertainment", "content", "publishing",
                       "broadcast", "video", "audio", "podcast"],
        "logistics":  ["logistics", "supply chain", "warehouse", "shipping",
                       "freight", "last mile", "inventory", "fleet"],
        "edtech":     ["edtech", "education", "learning", "curriculum", "lms",
                       "mooc"],
        "security":   ["cybersecurity", "infosec", "soc", "threat",
                       "vulnerability", "compliance", "gdpr", "penetration"],
        "ai_ml":      ["machine learning", "deep learning", "nlp",
                       "computer vision", "llm", "data science", "neural",
                       "artificial intelligence"],
        "devtools":   ["developer tools", "devtools", "platform",
                       "infrastructure", "devops", "sre", "kubernetes",
                       "terraform", "ci/cd"],
    }

    _DEGREE_LEVELS: list[tuple[re.Pattern, int]] = [
        (re.compile(r'\b(phd|ph\.d\.?|doctorate|doctoral)\b',          re.IGNORECASE), 4),
        (re.compile(r'\b(master|mba|msc|m\.s\.?|m\.eng|postgrad)\b',   re.IGNORECASE), 3),
        (re.compile(r'\b(bachelor|b\.s\.?|b\.e\.?|b\.tech|b\.sc|'
                    r'undergraduate|degree|graduate degree)\b',          re.IGNORECASE), 2),
        (re.compile(r'\b(associate.?s? degree|associate.?s?)\b',        re.IGNORECASE), 1),
    ]

    # ── Internal helpers ───────────────────────────────────────────────

    @classmethod
    def _seniority_tier(cls, text: str) -> int | None:
        """Return the highest seniority tier found in text, or None."""
        tl = text.lower()
        for phrase, tier in cls._SENIORITY_TIERS:
            if phrase in tl:
                return tier
        return None

    @classmethod
    def _candidate_yoe(cls, work_experiences: list) -> float:
        """Sum all work experience durations in decimal years."""
        today = _date.today()
        total_days = 0
        for exp in work_experiences:
            start = _to_date(getattr(exp, "start_date", None))
            if start is None:
                continue
            end = _to_date(getattr(exp, "end_date", None)) or today
            delta = (end - start).days
            if delta > 0:
                total_days += delta
        return total_days / 365.25

    @classmethod
    def _most_recent_exp(cls, work_experiences: list):
        """Return the work experience entry with the latest start_date."""
        if not work_experiences:
            return None
        return max(
            work_experiences,
            key=lambda e: _to_date(getattr(e, "start_date", None)) or _date.min,
        )

    @classmethod
    def _highest_degree(cls, educations: list) -> int:
        """Return the highest degree level (0–4) across all education entries."""
        best = 0
        for edu in educations:
            edu_text = " ".join(filter(None, [
                getattr(edu, "degree", None),
                getattr(edu, "field_of_study", None),
                getattr(edu, "institution", None),
            ])).lower()
            for pattern, level in cls._DEGREE_LEVELS:
                if pattern.search(edu_text):
                    best = max(best, level)
                    break
        return best

    # ── Public scorer ──────────────────────────────────────────────────

    @classmethod
    def score(
        cls,
        jd_analysis: dict[str, Any],
        profile_skills: list[str],
        jd_text: str = "",
        work_experiences: list | None = None,
        educations: list | None = None,
        role_title: str = "",
    ) -> tuple[float, dict[str, float]]:
        """
        Parameters
        ----------
        jd_analysis      : dict returned by analyze_jd (must have strengths,
                           unsupported_gaps, required_skills, preferred_skills,
                           suggested_keywords keys).
        profile_skills   : list of skill name strings from the Skill table.
        jd_text          : raw JD text — used for YoE regex and seniority.
        work_experiences : list of WorkExperience ORM objects.
        educations       : list of Education ORM objects.
        role_title       : target role title string.

        Returns
        -------
        (composite_fit_score, breakdown_dict)
        """
        work_experiences = work_experiences or []
        educations       = educations or []
        profile_lower    = [s.lower().strip() for s in profile_skills]

        def skill_match(target_list: list[str]) -> float:
            """Fraction of target skills found in profile (substring both ways)."""
            if not target_list:
                return 0.0
            target_lower = [s.lower().strip() for s in target_list]
            matched = sum(
                1 for t in target_lower
                if any(t in p or p in t for p in profile_lower)
            )
            return matched / len(target_lower)

        # ── 1. Required Skills Coverage (max 25 pts) ──────────────────
        required = jd_analysis.get("required_skills", [])
        req_pts  = skill_match(required) * 25.0 if required else 12.5

        # ── 2. Preferred Skills Coverage (max 10 pts) ─────────────────
        preferred = jd_analysis.get("preferred_skills", [])
        pref_pts  = skill_match(preferred) * 10.0 if preferred else 5.0

        # ── 3. Strengths / Gaps Ratio (max 15 pts) ────────────────────
        n_str  = len(jd_analysis.get("strengths", []))
        n_gap  = len(jd_analysis.get("unsupported_gaps", []))
        total  = n_str + n_gap
        sg_pts = (n_str / total) * 15.0 if total > 0 else 7.5

        # ── 4. Keyword Density Bonus (max 10 pts) ─────────────────────
        suggested = [k.lower().strip() for k in jd_analysis.get("suggested_keywords", [])]
        if suggested:
            # Build a broad corpus: skills + work descriptions + roles
            corpus_parts = [s.lower() for s in profile_skills]
            for exp in work_experiences:
                corpus_parts.append((getattr(exp, "description", "") or "").lower())
                corpus_parts.append((getattr(exp, "role", "") or "").lower())
            corpus = " ".join(corpus_parts)
            matched_kw = sum(1 for kw in suggested if kw in corpus)
            kw_pts = (matched_kw / len(suggested)) * 10.0
        else:
            kw_pts = 5.0

        # ── 5. Title / Seniority Alignment (max 15 pts) ───────────────
        target_tier = cls._seniority_tier(role_title) or cls._seniority_tier(jd_text[:400])
        recent_exp  = cls._most_recent_exp(work_experiences)
        cand_tier   = cls._seniority_tier(
            getattr(recent_exp, "role", "") or ""
        ) if recent_exp else None

        if target_tier is not None and cand_tier is not None:
            gap = abs(target_tier - cand_tier)
            seniority_pts = max(0.0, 15.0 - gap * 5.0)
        else:
            seniority_pts = 7.5  # neutral — can't determine

        # ── 6. Experience Depth / YoE Match (max 15 pts) ──────────────
        yoe_match = cls._YOE_RE.search(jd_text)
        if yoe_match and work_experiences:
            required_yoe  = int(yoe_match.group(1))
            candidate_yoe = cls._candidate_yoe(work_experiences)
            if required_yoe == 0:
                exp_pts = 15.0
            else:
                ratio   = candidate_yoe / required_yoe
                # Linear from 0 at 0 × to 15 at 1 ×; capped at 15 beyond 1 ×
                exp_pts = min(ratio * 15.0, 15.0)
        else:
            exp_pts = 7.5  # neutral

        # ── 7. Domain / Industry Alignment (max 7 pts) ────────────────
        jd_lower   = jd_text.lower()
        cand_corpus = " ".join(
            (getattr(e, "description", "") or "") + " " +
            (getattr(e, "company", "") or "")
            for e in work_experiences
        ).lower()

        best_overlap = 0.0
        for kw_list in cls._DOMAIN_CLUSTERS.values():
            jd_hits   = sum(1 for kw in kw_list if kw in jd_lower)
            if jd_hits >= 2:               # JD has a clear domain signal
                cand_hits = sum(1 for kw in kw_list if kw in cand_corpus)
                overlap   = min(cand_hits / jd_hits, 1.0) if jd_hits else 0.0
                best_overlap = max(best_overlap, overlap)

        domain_pts = best_overlap * 7.0 if best_overlap > 0 else 3.5

        # ── 8. Education Match (max 3 pts) ────────────────────────────
        req_degree = None
        for pattern, level in cls._DEGREE_LEVELS:
            if pattern.search(jd_text):
                req_degree = level
                break

        if req_degree is None:
            edu_pts = 1.5           # neutral — no degree mentioned
        elif not educations:
            edu_pts = 0.0           # degree required but none on profile
        else:
            cand_degree = cls._highest_degree(educations)
            if cand_degree >= req_degree:
                edu_pts = 3.0
            elif cand_degree == req_degree - 1:
                edu_pts = 1.5       # one level below — partial credit
            else:
                edu_pts = 0.0

        # ── Compose ───────────────────────────────────────────────────
        raw = (req_pts + pref_pts + sg_pts + kw_pts
               + seniority_pts + exp_pts + domain_pts + edu_pts)
        composite = round(min(max(raw, 0.0), 100.0), 1)

        breakdown = {
            "required_skills":  round(req_pts, 1),
            "preferred_skills": round(pref_pts, 1),
            "strengths_gaps":   round(sg_pts, 1),
            "keyword_density":  round(kw_pts, 1),
            "seniority_match":  round(seniority_pts, 1),
            "experience_depth": round(exp_pts, 1),
            "domain_alignment": round(domain_pts, 1),
            "education_match":  round(edu_pts, 1),
        }

        return composite, breakdown


# ── Competition Scorer ────────────────────────────────────────────────────────

class CompetitionScorer:
    """
    Estimates how competitive (hard to get) a role is, purely from JD text.
    Returns 0–100; higher = tougher competition.

    Factors:
      - Seniority keyword          ±  up to ±35
      - Years-of-experience regex  +  up to +25
      - Big-name company           +  20
      - Urgency signals            –  10  (in a hurry → easier to get)
    """

    _SENIORITY: dict[str, float] = {
        "intern":        -20, "internship":    -20,
        "entry level":   -15, "entry-level":   -15,
        "junior":        -10,
        "associate":      -5,
        "mid level":       0, "mid-level":       0,
        "senior":         15, "sr.":            15,
        "lead":           20,
        "staff":          25,
        "principal":      30, "architect":      25,
        "manager":        20, "director":       30,
        "vp":             35, "vice president": 35,
        "head of":        30,
    }

    _BIG_COMPANIES: frozenset[str] = frozenset({
        "google", "meta", "apple", "amazon", "microsoft", "netflix",
        "uber", "stripe", "airbnb", "openai", "anthropic", "deepmind",
        "salesforce", "oracle", "ibm", "nvidia", "tesla", "linkedin",
        "twitter", "x corp", "bytedance", "tiktok", "shopify", "atlassian",
        "palantir", "databricks", "snowflake", "figma",
    })

    _URGENCY: tuple[str, ...] = (
        "immediately", "asap", "urgent", "right away", "as soon as possible",
        "start immediately", "immediate start",
    )

    _YOE_RE = re.compile(r'(\d+)\+?\s*(?:to\s*\d+\s*)?years?', re.IGNORECASE)

    @classmethod
    def score(cls, jd_text: str, company_name: str = "") -> float:
        text    = jd_text.lower()
        company = company_name.lower()
        base    = 40.0

        for level, delta in sorted(
            cls._SENIORITY.items(), key=lambda x: -len(x[0])
        ):
            if level in text:
                base += delta
                break

        yoe_match = cls._YOE_RE.search(text)
        if yoe_match:
            years  = int(yoe_match.group(1))
            base  += min(years * 3.0, 25.0)

        if any(sig in company for sig in cls._BIG_COMPANIES):
            base += 20.0

        if any(sig in text for sig in cls._URGENCY):
            base -= 10.0

        return round(max(0.0, min(base, 100.0)), 1)


# ── Priority Scorer (composer) ────────────────────────────────────────────────

class PriorityScorer:
    """
    Composes fit and competition sub-scores into a final priority_score.

    Weights
    -------
      fit_score   50 %  — can you actually do the job?
      ease_score  50 %  — 100 - competition_score; is it winnable?
    """

    WEIGHTS = {"fit": 0.50, "ease": 0.50}

    @classmethod
    def compose(
        cls,
        fit_score: float,
        competition_score: float,
        fit_breakdown: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        ease     = 100.0 - competition_score
        priority = round(
            fit_score * cls.WEIGHTS["fit"] + ease * cls.WEIGHTS["ease"],
            1,
        )
        priority = max(0.0, min(priority, 100.0))

        if priority >= 70:
            recommendation, label = "Apply Now",         "strong"
        elif priority >= 45:
            recommendation, label = "Good Fit — Apply",  "good"
        else:
            recommendation, label = "Low Priority",      "weak"

        return {
            "priority_score":    priority,
            "fit_score":         fit_score,
            "competition_score": competition_score,
            "fit_breakdown":     fit_breakdown or {},
            "recommendation":    recommendation,
            "label":             label,
        }

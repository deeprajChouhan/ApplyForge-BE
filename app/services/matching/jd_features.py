"""Extract structured requirements from a raw job description.

Deterministic and cheap — regex + a curated skill dictionary. No LLM.
Runs once per Job on ingest and caches the result to `Job.jd_features_json`.
The scorer then reads that JSON per-tick without re-parsing.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import List, Tuple

from app.services.matching.scoring import JdFeatures, JdSkill

# Curated skills dictionary — canonical lowercase, aliases mapped in.
# Expand freely; membership decides which words the extractor counts.
SKILL_ALIASES: dict[str, list[str]] = {
    "python": ["python", "py"],
    "javascript": ["javascript", "js"],
    "typescript": ["typescript", "ts"],
    "java": ["java"],
    "go": ["golang", " go "],
    "rust": ["rust"],
    "c++": ["c++", "cpp"],
    "kotlin": ["kotlin"],
    "swift": ["swift"],
    "sql": ["sql"],
    "react": ["react", "reactjs", "react.js"],
    "nextjs": ["nextjs", "next.js"],
    "node": ["node.js", "nodejs", "node"],
    "django": ["django"],
    "fastapi": ["fastapi"],
    "flask": ["flask"],
    "spring": ["spring boot", "spring"],
    "postgres": ["postgres", "postgresql"],
    "mysql": ["mysql"],
    "mongodb": ["mongodb", "mongo"],
    "redis": ["redis"],
    "kafka": ["kafka"],
    "rabbitmq": ["rabbitmq"],
    "docker": ["docker"],
    "kubernetes": ["kubernetes", "k8s"],
    "terraform": ["terraform"],
    "aws": ["aws", "amazon web services"],
    "gcp": ["gcp", "google cloud"],
    "azure": ["azure"],
    "linux": ["linux"],
    "pytorch": ["pytorch"],
    "tensorflow": ["tensorflow"],
    "sklearn": ["sklearn", "scikit-learn"],
    "llm": ["llm", "large language model"],
    "rag": ["rag", "retrieval augmented"],
    "langchain": ["langchain"],
    "graphql": ["graphql"],
    "grpc": ["grpc"],
    "rest": ["rest api", "restful"],
    "microservices": ["microservices"],
    "distributed systems": ["distributed systems", "distributed system"],
    "system design": ["system design"],
    "ci/cd": ["ci/cd", "cicd"],
    "playwright": ["playwright"],
    "selenium": ["selenium"],
    "spark": ["spark", "apache spark"],
    "airflow": ["airflow"],
    "snowflake": ["snowflake"],
    "dbt": ["dbt"],
    "elasticsearch": ["elasticsearch", "elastic search"],
}

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "fintech": ["fintech", "payments", "banking", "trading", "credit", "lending"],
    "ml": ["machine learning", "deep learning", "ml infrastructure", "model training", "llm"],
    "devtools": ["developer tools", "sdk", "cli", "developer experience", "dx"],
    "backend": ["backend", "server-side", "api"],
    "frontend": ["frontend", "front-end", "ui engineering"],
    "data": ["data engineering", "etl", "warehouse", "analytics"],
    "security": ["security", "cryptography", "auth", "compliance"],
    "infra": ["infrastructure", "sre", "reliability", "platform engineering"],
    "healthcare": ["healthcare", "clinical", "biotech"],
    "ecommerce": ["ecommerce", "e-commerce", "marketplace"],
}

ROLE_FAMILIES: list[tuple[str, list[str]]] = [
    ("ml_engineer", ["ml engineer", "machine learning engineer", "ai engineer"]),
    ("data_engineer", ["data engineer"]),
    ("data_scientist", ["data scientist"]),
    ("backend_engineer", ["backend engineer", "server engineer", "api engineer"]),
    ("frontend_engineer", ["frontend engineer", "ui engineer"]),
    ("fullstack_engineer", ["fullstack", "full stack", "full-stack"]),
    ("devops", ["devops", "sre", "site reliability", "platform engineer"]),
    ("security_engineer", ["security engineer", "appsec"]),
    ("mobile_engineer", ["ios engineer", "android engineer", "mobile engineer"]),
    ("engineering_manager", ["engineering manager", "eng manager", "tech lead"]),
]

SENIORITY_PATTERNS: list[tuple[str, list[str]]] = [
    ("principal", ["principal"]),
    ("staff", ["staff"]),
    ("lead", ["lead", "technical lead"]),
    ("senior", ["senior", "sr."]),
    ("mid", ["mid-level", "mid level"]),
    ("entry", ["entry-level", "entry level", "junior", "jr."]),
    ("intern", ["intern", "internship"]),
]


def _find_years_required(text: str) -> int:
    """Best-effort extraction of "X+ years" from JD text."""
    m = re.findall(r"(\d{1,2})\+?\s*(?:\+)?\s*years?", text)
    if not m:
        return 0
    # Take the smallest — JDs often say "5+ years for backend, 3+ for frontend"
    # and the *floor* is the honest requirement.
    years = [int(x) for x in m if x.isdigit()]
    return min(years) if years else 0


def _split_sections(text: str) -> tuple[str, str, str]:
    """Split JD into (requirements, nice_to_have, other) by common headers."""
    t = text
    lower = t.lower()
    req_start = max(lower.find("requirements"), lower.find("qualifications"), lower.find("what you'll bring"), lower.find("what we're looking for"))
    nice_start = max(lower.find("nice to have"), lower.find("nice-to-have"), lower.find("bonus"), lower.find("preferred qualifications"), lower.find("preferred"))

    if req_start == -1 and nice_start == -1:
        return "", "", t

    if req_start != -1 and (nice_start == -1 or nice_start > req_start):
        # requirements before nice-to-have
        req_end = nice_start if nice_start != -1 else len(t)
        req = t[req_start:req_end]
        nice = t[nice_start:] if nice_start != -1 else ""
        other = t[:req_start]
    else:
        # nice-to-have appears first (rare); treat whole tail as requirements
        req = t[req_start:] if req_start != -1 else t
        nice = t[nice_start:req_start] if req_start != -1 else t[nice_start:]
        other = t[:min(x for x in [req_start, nice_start] if x != -1)]

    return req, nice, other


def _count_skill_hits(text: str) -> Counter[str]:
    """Return canonical_skill -> count of matches in text (case-insensitive)."""
    text_l = text.lower()
    out: Counter[str] = Counter()
    for canonical, aliases in SKILL_ALIASES.items():
        for alias in aliases:
            # Word-boundary safe for most aliases; exact substring for c++/k8s etc.
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text_l):
                out[canonical] += text_l.count(alias)
    return out


def _skills_with_weights(counts: Counter[str]) -> list[JdSkill]:
    """Turn raw hit counts into weighted JdSkills. Cap weight at 1.0.
    A skill mentioned once = 0.4, 5+ times = 1.0.
    """
    return [
        JdSkill(name=name, weight=round(min(1.0, 0.4 + 0.15 * (n - 1)), 2))
        for name, n in counts.items()
        if n > 0
    ]


def _detect_domains(text: str) -> list[str]:
    text_l = text.lower()
    return [d for d, kws in DOMAIN_KEYWORDS.items() if any(k in text_l for k in kws)]


def _detect_role_family(title: str, text: str) -> str:
    t = (title + " " + text[:500]).lower()
    for family, patterns in ROLE_FAMILIES:
        if any(p in t for p in patterns):
            return family
    return ""


def _detect_seniority(title: str, text: str) -> str:
    t = (title + " " + text[:500]).lower()
    for level, patterns in SENIORITY_PATTERNS:
        if any(p in t for p in patterns):
            return level
    return "mid"


def extract_jd_features(title: str, description: str) -> JdFeatures:
    """One-shot extraction. Cheap enough to run per Job at ingest time."""
    req_section, nice_section, other = _split_sections(description or "")
    # If the JD has explicit sections, weight must-haves from that section.
    # Otherwise treat the whole JD as one bucket of must-haves.
    if req_section:
        must = _skills_with_weights(_count_skill_hits(req_section + " " + other))
    else:
        must = _skills_with_weights(_count_skill_hits(description or ""))
    nice = _skills_with_weights(_count_skill_hits(nice_section)) if nice_section else []

    # De-dupe: if a skill appears in both, keep the higher weight in must-have.
    must_names = {s.name for s in must}
    nice = [n for n in nice if n.name not in must_names]

    return JdFeatures(
        must_have_skills=must,
        nice_to_have_skills=nice,
        min_years_experience=_find_years_required(description or ""),
        seniority=_detect_seniority(title or "", description or ""),
        domain_tags=_detect_domains(description or ""),
        role_family=_detect_role_family(title or "", description or ""),
    )


def features_to_json(features: JdFeatures) -> dict:
    """Serialize for storage in `Job.jd_features_json`."""
    return {
        "must_have_skills": [{"name": s.name, "weight": s.weight} for s in features.must_have_skills],
        "nice_to_have_skills": [{"name": s.name, "weight": s.weight} for s in features.nice_to_have_skills],
        "min_years_experience": features.min_years_experience,
        "seniority": features.seniority,
        "domain_tags": features.domain_tags,
        "role_family": features.role_family,
    }


def features_from_json(payload: dict | None) -> JdFeatures:
    if not payload:
        return JdFeatures()
    return JdFeatures(
        must_have_skills=[JdSkill(name=s["name"], weight=s["weight"]) for s in payload.get("must_have_skills", [])],
        nice_to_have_skills=[JdSkill(name=s["name"], weight=s["weight"]) for s in payload.get("nice_to_have_skills", [])],
        min_years_experience=payload.get("min_years_experience", 0),
        seniority=payload.get("seniority", "mid"),
        domain_tags=payload.get("domain_tags", []),
        role_family=payload.get("role_family", ""),
    )

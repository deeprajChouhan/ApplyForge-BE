"""
Skill vocabulary for CV parsing and role matching. Normalises aliases to a
canonical lowercase token so "js"/"reactjs" resolve like "javascript"/"react".
Extend freely — matching quality scales with coverage.
"""
from __future__ import annotations

import re

_SKILL_ALIASES: dict[str, list[str]] = {
    "python": ["py"],
    "javascript": ["js", "ecmascript"],
    "typescript": ["ts"],
    "react": ["reactjs", "react.js"],
    "next.js": ["nextjs", "next"],
    "node.js": ["node", "nodejs"],
    "java": [],
    "c++": ["cpp"],
    "c#": ["csharp"],
    "go": ["golang"],
    "rust": [],
    "ruby": [],
    "php": [],
    "sql": [],
    "postgresql": ["postgres", "psql"],
    "mysql": [],
    "mongodb": ["mongo"],
    "redis": [],
    "aws": ["amazon web services"],
    "gcp": ["google cloud"],
    "azure": [],
    "docker": [],
    "kubernetes": ["k8s"],
    "terraform": [],
    "graphql": [],
    "rest": ["rest api", "restful"],
    "fastapi": [],
    "django": [],
    "flask": [],
    "spring": ["spring boot"],
    "tensorflow": [],
    "pytorch": [],
    "machine learning": ["ml"],
    "deep learning": [],
    "nlp": ["natural language processing"],
    "data science": [],
    "pandas": [],
    "numpy": [],
    "tailwind": ["tailwindcss"],
    "html": [],
    "css": [],
    "git": [],
    "ci/cd": ["cicd"],
    "figma": [],
    "product management": [],
    "project management": [],
    "agile": ["scrum"],
    "salesforce": [],
    "excel": [],
    # ── Sales & business development ──
    "sales": ["selling"],
    "account management": ["account manager"],
    "business development": ["biz dev"],
    "lead generation": ["lead gen"],
    "negotiation": ["negotiating"],
    "cold calling": ["cold outreach"],
    "pipeline management": [],
    "crm": [],
    "quota attainment": ["quota"],
    # ── Finance & accounting ──
    "financial analysis": [],
    "financial modeling": ["financial modelling"],
    "accounting": ["accountancy"],
    "forecasting": ["financial forecasting"],
    "budgeting": ["budget management"],
    "fp&a": ["financial planning and analysis"],
    "valuation": [],
    "auditing": ["audit"],
    "quickbooks": [],
    "gaap": [],
    # ── Marketing ──
    "digital marketing": [],
    "seo": ["search engine optimization"],
    "sem": ["search engine marketing"],
    "content marketing": [],
    "social media": ["social media marketing"],
    "email marketing": [],
    "google analytics": [],
    "hubspot": [],
    "copywriting": [],
    "brand management": ["branding"],
    "campaign management": [],
    "paid advertising": ["paid ads", "ppc"],
    # ── Operations & supply chain ──
    "operations management": ["operations"],
    "supply chain": ["supply chain management"],
    "logistics": [],
    "process improvement": [],
    "six sigma": ["lean six sigma"],
    "vendor management": [],
    "inventory management": [],
    # ── HR & people ──
    "recruiting": ["recruitment"],
    "talent acquisition": [],
    "onboarding": [],
    "employee relations": [],
    "performance management": [],
    "compensation and benefits": ["comp and benefits"],
    "hris": [],
    # ── Customer success & support ──
    "customer success": [],
    "customer support": ["customer service"],
    "churn reduction": ["churn"],
    "saas": [],
    # ── Cross-functional professional skills ──
    "leadership": [],
    "communication": [],
    "stakeholder management": [],
    "data analysis": ["data analytics"],
    "tableau": [],
    "powerpoint": [],
    "public speaking": [],
}

_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canon, _aliases in _SKILL_ALIASES.items():
    _ALIAS_TO_CANONICAL[_canon] = _canon
    for _a in _aliases:
        _ALIAS_TO_CANONICAL[_a] = _canon

_ORDERED_TERMS: list[tuple[str, str]] = sorted(
    _ALIAS_TO_CANONICAL.items(), key=lambda kv: -len(kv[0])
)


def normalize_skill(raw: str) -> str:
    key = raw.strip().lower()
    return _ALIAS_TO_CANONICAL.get(key, key)


_BOUNDARY_CACHE: dict[str, re.Pattern[str]] = {}


def _term_pattern(term: str) -> re.Pattern[str]:
    pat = _BOUNDARY_CACHE.get(term)
    if pat is None:
        # Word-boundary match that tolerates symbol-y skills (c++, c#, node.js,
        # ci/cd). v1 used raw substring search, so "go" matched "good" and
        # "ts" matched "results".
        pat = re.compile(r"(?<![a-z0-9+#])" + re.escape(term) + r"(?![a-z0-9+#]|\.[a-z0-9])")
        _BOUNDARY_CACHE[term] = pat
    return pat


# Very short / ambiguous aliases that are only trusted when listed as a skill,
# never when found in free text ("go to market", "next steps", "node" …).
_TEXT_UNSAFE = {"go", "py", "js", "ts", "ml", "next", "node", "rest", "quota",
                "churn", "operations", "branding", "selling", "scrum"}


def extract_skills(text: str) -> list[str]:
    if not text:
        return []
    hay = text.lower()
    found: list[str] = []
    seen: set[str] = set()
    for term, canon in _ORDERED_TERMS:
        if canon in seen or term in _TEXT_UNSAFE:
            continue
        if term in hay and _term_pattern(term).search(hay):
            found.append(canon)
            seen.add(canon)
    return found


# Adjacent skills that earn partial credit in matching (symmetric).
_RELATED: dict[str, set[str]] = {}
for _group in (
    {"sql", "postgresql", "mysql"},
    {"javascript", "typescript"},
    {"react", "next.js"},
    {"aws", "gcp", "azure"},
    {"docker", "kubernetes"},
    {"machine learning", "deep learning", "pytorch", "tensorflow", "data science"},
    {"pandas", "numpy", "data analysis", "data science"},
    {"django", "flask", "fastapi"},
    {"tableau", "data analysis", "excel"},
    {"recruiting", "talent acquisition"},
    {"customer success", "account management"},
    {"sales", "business development", "lead generation"},
    {"accounting", "auditing", "gaap"},
    {"financial analysis", "financial modeling", "fp&a", "forecasting", "budgeting"},
    {"seo", "sem", "digital marketing", "paid advertising"},
    {"project management", "product management", "agile"},
    {"supply chain", "logistics", "inventory management"},
    {"salesforce", "crm", "hubspot"},
):
    for _s in _group:
        _RELATED.setdefault(_s, set()).update(_group - {_s})


def related_skills(skill: str) -> set[str]:
    return _RELATED.get(normalize_skill(skill), set())

"""
Skill vocabulary for CV parsing and role matching. Normalises aliases to a
canonical lowercase token so "js"/"reactjs" resolve like "javascript"/"react".
Extend freely — matching quality scales with coverage.
"""
from __future__ import annotations

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


def extract_skills(text: str) -> list[str]:
    if not text:
        return []
    hay = " " + text.lower() + " "
    found: list[str] = []
    seen: set[str] = set()
    for term, canon in _ORDERED_TERMS:
        if term in hay and canon not in seen:
            found.append(canon)
            seen.add(canon)
    return found

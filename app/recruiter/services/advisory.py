"""
Company → next hire (Phase 4, Section 3.2, direction 2).

Advisory, not prediction-by-ML: infer a client's likely next hire from their own
roster plus agency benchmarks. Deterministic and rule/benchmark-driven — skills
common across the agency's book but absent from this client, a seniority-gap
check, and the pool supply behind each suggestion. An LLM reasoning layer can
phrase these later; the signals are computed here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.recruiter.models import CandidateProfile, Client, Role

# Skill clusters → a suggested role archetype title.
_ARCHETYPES: list[tuple[str, set[str]]] = [
    ("Backend Engineer", {"python", "fastapi", "django", "flask", "node.js", "go", "java", "postgresql", "rest"}),
    ("Frontend Engineer", {"react", "next.js", "typescript", "javascript", "tailwind", "html", "css"}),
    ("ML Engineer", {"machine learning", "deep learning", "nlp", "tensorflow", "pytorch", "data science"}),
    ("DevOps / Platform Engineer", {"docker", "kubernetes", "terraform", "aws", "gcp", "azure", "ci/cd"}),
    ("Data Engineer", {"sql", "pandas", "numpy", "postgresql", "mysql", "mongodb", "redis"}),
    ("Product Manager", {"product management", "project management", "agile"}),
]

_SENIOR_TERMS = {"senior", "lead", "principal", "staff", "head", "director", "vp", "chief"}


@dataclass
class NextHireSuggestion:
    title: str
    rationale: str
    skills: list[str]
    pool_supply: int
    confidence: str


@dataclass
class NextHireAdvisory:
    client_id: int
    client_name: str
    roster_roles: int
    suggestions: list[NextHireSuggestion] = field(default_factory=list)
    seniority_note: str | None = None


def _archetype_for(skills: list[str]) -> str:
    best_title, best_hits = None, 0
    sset = set(skills)
    for title, cluster in _ARCHETYPES:
        hits = len(sset & cluster)
        if hits > best_hits:
            best_title, best_hits = title, hits
    if best_title:
        return best_title
    return f"{skills[0].title()} Specialist" if skills else "Generalist hire"


def _is_senior(role: Role) -> bool:
    hay = f"{role.seniority or ''} {role.title or ''}".lower()
    return any(t in hay for t in _SENIOR_TERMS)


def next_hire_advisory(db: Session, agency_id: int, client: Client) -> NextHireAdvisory:
    agency_roles = db.query(Role).filter(Role.agency_id == agency_id).all()
    client_roles = [r for r in agency_roles if r.client_id == client.id]

    # Benchmark demand across the whole agency book.
    bench: dict[str, int] = {}
    for r in agency_roles:
        for s in set((r.required_skills or []) + (r.preferred_skills or [])):
            bench[s] = bench.get(s, 0) + 1

    client_skills: set[str] = set()
    for r in client_roles:
        client_skills |= set((r.required_skills or []) + (r.preferred_skills or []))

    # Pool supply per skill.
    supply: dict[str, int] = {}
    for cand in db.query(CandidateProfile).filter(CandidateProfile.agency_id == agency_id).all():
        for cs in {s.name for s in cand.skills}:
            supply[cs] = supply.get(cs, 0) + 1

    advisory = NextHireAdvisory(
        client_id=client.id,
        client_name=client.name,
        roster_roles=len(client_roles),
    )

    if not client_roles:
        # Cold start: lean entirely on the agency's most in-demand skills.
        top = [s for s, _ in sorted(bench.items(), key=lambda kv: -kv[1])[:5]]
        if not top:
            return advisory
        pool = _pool_covering(db, agency_id, top)
        advisory.suggestions.append(
            NextHireSuggestion(
                title=_archetype_for(top),
                rationale=(
                    f"No roles yet for {client.name}. These are the skills most in demand across "
                    f"your agency — a strong place to start the engagement."
                ),
                skills=top,
                pool_supply=pool,
                confidence="medium" if pool else "low",
            )
        )
        return advisory

    # Gap skills: common across the book but absent from this client.
    gaps = sorted(
        [(s, c) for s, c in bench.items() if s not in client_skills], key=lambda kv: -kv[1]
    )
    top_gaps = [s for s, _ in gaps[:5]]
    if top_gaps:
        pool = _pool_covering(db, agency_id, top_gaps)
        strong = gaps[0][1] >= 2
        confidence = "high" if (strong and pool) else "medium" if pool else "low"
        advisory.suggestions.append(
            NextHireSuggestion(
                title=_archetype_for(top_gaps),
                rationale=(
                    f"These skills appear across your other roles but not yet in {client.name}'s "
                    f"roster — a likely gap to fill next."
                ),
                skills=top_gaps,
                pool_supply=pool,
                confidence=confidence,
            )
        )

    # Seniority gap check.
    if not any(_is_senior(r) for r in client_roles) and any(_is_senior(r) for r in agency_roles):
        advisory.seniority_note = (
            f"{client.name} has no senior/lead role yet, while your wider book does — "
            f"a senior hire may be the next step."
        )

    return advisory


def _pool_covering(db: Session, agency_id: int, skills: list[str]) -> int:
    """How many pool candidates have at least one of these skills."""
    wanted = set(skills)
    count = 0
    for cand in db.query(CandidateProfile).filter(CandidateProfile.agency_id == agency_id).all():
        if {s.name for s in cand.skills} & wanted:
            count += 1
    return count

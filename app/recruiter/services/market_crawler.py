"""
Recruiter-side market intelligence crawler.

Distinct from the consumer job crawler (`app.services.crawler`) which fetches
postings for individual job-seekers. This one aggregates external role listings
into compensation percentiles + demanded-skill signals scoped to a role/title +
location, and stores the result as a MarketSnapshot the role-detail page can
render alongside the client's budget for negotiation context.

Current status: scaffolding + heuristic fallback. External source fetchers are
registered by name so a scheduled task can enable them one at a time; when no
source returns data, we synthesise a snapshot from the agency's own historical
roles + shortlists so the UI is never empty. Every snapshot records which
sources contributed, so the role page can show provenance.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable

from sqlalchemy.orm import Session

from app.recruiter.models import MarketSnapshot, Role
from app.recruiter.services.skills import normalize_skill
from app.services.crawler.sources import fetch_arbeitnow, fetch_remoteok

logger = logging.getLogger(__name__)


# ── Salary parsing ─────────────────────────────────────────────────────────
# Consumer crawler returns salary as a human string ("$120,000 – $160,000",
# "£70k – £90k", "80000+"). Parse both anchors to ints for percentile math.
_NUM_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(k|K)?")


def _parse_salary_pair(text: str | None) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    nums: list[int] = []
    for m in _NUM_RE.finditer(text):
        raw = m.group(1).replace(",", "")
        try:
            val = float(raw)
        except ValueError:
            continue
        if m.group(2):  # 'k' suffix
            val *= 1000
        # Ignore tiny numbers (like 401k, 2025, etc.) and giant garbage.
        if 5_000 <= val <= 5_000_000:
            nums.append(int(val))
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], nums[0]
    return min(nums), max(nums)


@dataclass
class ExternalPosting:
    """Normalised row a source adapter must produce."""
    title: str
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str = "USD"
    skills: list[str] = field(default_factory=list)
    source: str = ""


SourceFetcher = Callable[[str, str | None], list[ExternalPosting]]


# ── Source registry ─────────────────────────────────────────────────────────
# Adapters are wired in one at a time as we build them. Each takes (query,
# location) and returns a list of ExternalPosting. Failing fetchers are logged
# and skipped — one bad source never kills the crawl.

def _country_from_location(location: str | None) -> str | None:
    """Very light heuristic — turn a role location into a 2-letter country hint."""
    if not location:
        return None
    low = location.lower()
    for code, kws in {
        "gb": ("uk", "united kingdom", "london", "manchester"),
        "us": ("usa", "united states", "new york", "san francisco"),
        "in": ("india", "bengaluru", "bangalore", "mumbai", "delhi"),
        "de": ("germany", "berlin", "munich"),
        "ca": ("canada", "toronto", "vancouver"),
    }.items():
        if any(k in low for k in kws):
            return code
    return None


def _fetch_remoteok(query: str, location: str | None) -> list[ExternalPosting]:
    """Free, keyless. Salary strings are fuzzy but usable in aggregate."""
    jobs = fetch_remoteok([query], work_type="any", country=_country_from_location(location))
    out: list[ExternalPosting] = []
    for j in jobs:
        lo, hi = _parse_salary_pair(j.get("salary_range"))
        tags = [normalize_skill(t) for t in (j.get("tags") or []) if t]
        out.append(
            ExternalPosting(
                title=j.get("title") or "",
                salary_min=lo,
                salary_max=hi,
                currency="USD",
                skills=[t for t in tags if t],
                source="remoteok",
            )
        )
    return out


def _fetch_arbeitnow(query: str, location: str | None) -> list[ExternalPosting]:
    """Global board, paginated. Salaries are frequently blank — we still keep
    titles + tags for the demand signal."""
    jobs = fetch_arbeitnow([query], work_type="any", country=_country_from_location(location))
    out: list[ExternalPosting] = []
    for j in jobs:
        lo, hi = _parse_salary_pair(j.get("salary_range"))
        tags = [normalize_skill(t) for t in (j.get("tags") or []) if t]
        out.append(
            ExternalPosting(
                title=j.get("title") or "",
                salary_min=lo,
                salary_max=hi,
                currency="EUR",  # Arbeitnow tilts EU; downstream code respects role currency
                skills=[t for t in tags if t],
                source="arbeitnow",
            )
        )
    return out


def _fetch_adzuna(query: str, location: str | None) -> list[ExternalPosting]:  # pragma: no cover
    # Kept as a stub — Adzuna needs an API key. Wire when credentials are provisioned.
    return []


_SOURCES: dict[str, SourceFetcher] = {
    "remoteok": _fetch_remoteok,
    "arbeitnow": _fetch_arbeitnow,
    "adzuna": _fetch_adzuna,
}


# ── Aggregation ─────────────────────────────────────────────────────────────

def _percentiles(values: list[int]) -> tuple[int | None, int | None, int | None]:
    if not values:
        return None, None, None
    values = sorted(values)
    n = len(values)

    def pick(pct: float) -> int:
        idx = min(n - 1, max(0, int(round(pct * (n - 1)))))
        return int(values[idx])

    return pick(0.25), pick(0.50), pick(0.75)


def _top_n(counter: dict[str, int], n: int) -> list[str]:
    return [k for k, _ in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def _synthesise_from_own_pool(db: Session, agency_id: int, query: str) -> tuple[list[int], list[str], list[str]]:
    """
    Fallback when no external source returns data: use the agency's own roles
    with matching titles to sketch a compensation band. This keeps the UI
    honest — the snapshot marks its source as "internal-only" so recruiters
    know it's not market-wide.
    """
    q = query.strip().lower()
    roles = db.query(Role).filter(Role.agency_id == agency_id).all()
    matches = [r for r in roles if q and q in (r.title or "").lower()]
    salaries: list[int] = []
    skills: dict[str, int] = {}
    titles: dict[str, int] = {}
    for r in matches:
        if r.salary_min:
            salaries.append(r.salary_min)
        if r.salary_max:
            salaries.append(r.salary_max)
        for s in (r.required_skills or []) + (r.preferred_skills or []):
            skills[s] = skills.get(s, 0) + 1
        if r.title:
            titles[r.title] = titles.get(r.title, 0) + 1
    return salaries, _top_n(skills, 8), _top_n(titles, 5)


def crawl_role_market(
    db: Session,
    agency_id: int,
    *,
    role: Role | None = None,
    query: str | None = None,
    location: str | None = None,
    sources: Iterable[str] | None = None,
) -> MarketSnapshot:
    """Fetch, aggregate, and persist a market snapshot. Always returns a row."""
    if role and not query:
        query = role.title
    if not query:
        raise ValueError("crawl_role_market requires a query or a role")
    if not location and role:
        location = role.location

    used_sources: list[str] = []
    postings: list[ExternalPosting] = []
    selected = list(sources) if sources is not None else list(_SOURCES.keys())
    for name in selected:
        fetcher = _SOURCES.get(name)
        if not fetcher:
            continue
        try:
            batch = fetcher(query, location)
        except Exception:  # pragma: no cover — never let one source crash the crawl
            logger.exception("market source %s failed", name)
            continue
        if batch:
            used_sources.append(name)
            postings.extend(batch)

    salaries: list[int] = []
    for p in postings:
        if p.salary_min:
            salaries.append(p.salary_min)
        if p.salary_max:
            salaries.append(p.salary_max)

    skill_counter: dict[str, int] = {}
    title_counter: dict[str, int] = {}
    for p in postings:
        for s in p.skills:
            skill_counter[s] = skill_counter.get(s, 0) + 1
        if p.title:
            title_counter[p.title] = title_counter.get(p.title, 0) + 1

    top_skills = _top_n(skill_counter, 8)
    competing = _top_n(title_counter, 5)

    if not postings:
        salaries, top_skills, competing = _synthesise_from_own_pool(db, agency_id, query)
        used_sources = ["internal-only"] if salaries else []

    p25, p50, p75 = _percentiles(salaries)

    snap = MarketSnapshot(
        agency_id=agency_id,
        role_id=role.id if role else None,
        query=query,
        location=location,
        sample_size=len(postings) if postings else len(salaries) // 2,
        salary_p25=p25,
        salary_p50=p50,
        salary_p75=p75,
        currency=(role.budget_currency if role else "USD"),
        top_skills=top_skills or None,
        competing_roles=competing or None,
        sources=used_sources or None,
    )
    db.add(snap)

    if role:
        # Cache the aggregate on the role too, so the client-shareable draft
        # view doesn't have to join across snapshots.
        role.market_snapshot = {
            "sample_size": snap.sample_size,
            "salary_p25": snap.salary_p25,
            "salary_p50": snap.salary_p50,
            "salary_p75": snap.salary_p75,
            "currency": snap.currency,
            "top_skills": snap.top_skills,
            "competing_roles": snap.competing_roles,
            "sources": snap.sources,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    db.commit()
    db.refresh(snap)
    return snap


def crawl_agency_market(
    db: Session,
    agency_id: int,
    *,
    max_queries: int = 6,
) -> list[MarketSnapshot]:
    """
    Fan-out: crawl the top-N most common titles across the agency's open,
    non-draft roles. Skips roles missing a title. Returns the snapshots created.
    """
    from collections import Counter
    from app.recruiter.enums import RoleStatus

    roles = (
        db.query(Role)
        .filter(Role.agency_id == agency_id, Role.status == RoleStatus.open, Role.is_draft.is_(False))
        .all()
    )
    counter: Counter[str] = Counter()
    role_by_title: dict[str, Role] = {}
    for r in roles:
        if not r.title:
            continue
        key = r.title.strip()
        counter[key] += 1
        role_by_title.setdefault(key, r)  # pick the first role for a title as the "context"

    snaps: list[MarketSnapshot] = []
    for title, _n in counter.most_common(max_queries):
        try:
            snap = crawl_role_market(db, agency_id, role=role_by_title.get(title), query=title)
            snaps.append(snap)
        except Exception:  # pragma: no cover
            logger.exception("agency market crawl failed for title=%s", title)
            continue
    return snaps

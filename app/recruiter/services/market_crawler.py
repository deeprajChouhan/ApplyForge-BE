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
    text: str = ""            # description (plain text) for relevance scoring
    location: str = ""
    country: str | None = None  # ISO-2 of the posting's market, None = remote/global


# ── Research profile: what exactly are we researching? ─────────────────────
# The old crawler searched the raw title and the board adapters then matched
# ANY word of it ("engineer", "gateway"…), so an "Embedded Linux / IoT Gateway
# Firmware Engineer" role pulled in SRE directors and sales reps. We now derive
# a domain profile from the role (title segments + required skills, optionally
# refined by the LLM), search with specific queries, and keep only postings
# that actually match the domain.

_GENERIC_TITLE_WORDS = {
    "engineer", "engineers", "engineering", "developer", "developers", "development",
    "senior", "sr", "junior", "jr", "mid", "lead", "principal", "staff", "head",
    "chief", "manager", "specialist", "consultant", "associate", "architect",
    "intern", "trainee", "ii", "iii", "iv", "i", "level", "remote", "hybrid",
    "onsite", "contract", "permanent", "full", "time", "part", "role", "team",
    "the", "and", "or", "of", "for", "with", "in", "at", "to", "a", "an", "&",
    "expert", "professional", "officer", "executive", "director", "vp",
}

# Tags boards attach that are not skills.
_NON_SKILL_TAGS = {
    "engineer", "engineering", "dev", "developer", "development", "exec", "executive",
    "senior", "junior", "lead", "manager", "management", "remote", "full time",
    "part time", "contract", "digital nomad", "legion", "non tech", "technical",
    "tech", "software", "saas", "startup", "marketing", "sales", "design", "support",
    "customer support", "admin", "finance", "hr", "recruiter", "operations", "other",
    "english", "german", "it", "web", "api", "cloud", "backend", "frontend", "full stack",
}

# Short skills that are also English words — matched case-sensitively as the
# acronym/proper form only ("CAN" not "can", "Go" not "go").
_CASE_SENSITIVE_SKILLS = {"can": ["CAN", "CAN bus", "CANbus", "CAN-bus"], "go": ["Go ", "Golang"],
                          "c": ["C,", "C/", " C ", "C."], "r": [" R "], "rest": ["REST"],
                          "ai": ["AI"], "ota": ["OTA"], "lte": ["LTE"], "gsm": ["GSM"], "ble": ["BLE"],
                          "tls": ["TLS"], "rpi": ["RPi", "Raspberry Pi"]}


@dataclass
class ResearchProfile:
    queries: list[str]
    domain_terms: list[str]       # at least one must appear in a posting's title
    core_skills: list[str]        # required skills (drive relevance + demand %)
    nice_skills: list[str]        # preferred skills
    exclude_terms: list[str]      # title terms that disqualify a posting
    country: str | None           # ISO-2 of the role's market
    currency: str
    used_llm: bool = False


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _title_segments(title: str) -> list[list[str]]:
    parts = re.split(r"[/|,()&+]|\s-\s|\band\b", title or "", flags=re.I)
    out: list[list[str]] = []
    for p in parts:
        words = [w for w in re.findall(r"[a-z0-9#+.]+", p.lower()) if w not in _GENERIC_TITLE_WORDS]
        if words:
            out.append(words)
    return out


def _heuristic_profile(role: Role, country: str | None, currency: str) -> ResearchProfile:
    segs = _title_segments(role.title or "")
    domain_phrases = [" ".join(w) for w in segs]
    domain_words = [w for seg in segs for w in seg if len(w) > 2 or w in ("c", "c++", "c#", "go", "ai")]
    req = [normalize_skill(s) for s in (role.required_skills or []) if s]
    pref = [normalize_skill(s) for s in (role.preferred_skills or []) if s]
    # A bare skill word in the title ("linux") is not a domain anchor on its
    # own — "Linux Sysadmin" isn't comparable to "Embedded Linux Engineer".
    # Keep it only if it's the sole anchor we have.
    skillset = set(req) | set(pref)
    anchors = [w for w in domain_words if w not in skillset]
    domain_words = anchors or domain_words

    queries: list[str] = []
    for seg in segs:
        # "embedded linux engineer", "iot gateway firmware engineer", then the
        # tighter tail ("firmware engineer") which boards index well.
        queries.append(" ".join(seg) + " engineer")
        if len(seg) > 1:
            queries.append(seg[-1] + " engineer")
    if req:
        queries.append(f"{req[0]} {domain_words[0] if domain_words else ''}".strip())
    queries = list(dict.fromkeys(q for q in queries if q.strip()))[:5]
    return ResearchProfile(
        queries=queries or [role.title or ""],
        domain_terms=list(dict.fromkeys(domain_phrases + domain_words)),
        core_skills=list(dict.fromkeys(req)),
        nice_skills=list(dict.fromkeys(pref)),
        exclude_terms=[],
        country=country,
        currency=currency,
    )


_PROFILE_SYSTEM = (
    "You plan job-market research for a recruiter. Given a role, output the "
    "search queries a recruiter would type into job boards to find COMPARABLE "
    "postings (same domain, same kind of work), the domain terms such postings "
    "must mention in their title, and title terms that indicate an unrelated "
    "job. Return STRICT JSON only."
)
_PROFILE_SCHEMA = (
    'Return JSON: {"queries": [string, ...3-5 short board search strings], '
    '"domain_terms": [string, ...lowercase words/phrases e.g. "embedded", "firmware"], '
    '"exclude_title_terms": [string, ...lowercase e.g. "sales", "recruiter"]}'
)


def build_research_profile(role: Role, use_llm: bool = True) -> ResearchProfile:
    country = (role.country_code or "").lower() or _country_from_location(role.location)
    currency = role.budget_currency or _CURRENCY_BY_COUNTRY.get(country or "", "USD")
    prof = _heuristic_profile(role, country, currency)
    if not use_llm:
        return prof
    try:
        from app.recruiter.services import ai_support

        if not ai_support.llm_enabled():
            return prof
        ctx = (
            f"Title: {role.title}\nSeniority: {role.seniority or '-'}\n"
            f"Location: {role.location or '-'} ({role.country_code or '-'})\n"
            f"Required skills: {', '.join(role.required_skills or [])}\n"
            f"Preferred skills: {', '.join((role.preferred_skills or [])[:20])}\n"
            f"Description: {(role.description or '')[:1500]}"
        )
        data = ai_support.generate_json(_PROFILE_SYSTEM, _PROFILE_SCHEMA + "\n\n" + ctx)
    except Exception:  # pragma: no cover
        logger.exception("market research profile LLM failed")
        return prof
    if isinstance(data, dict):
        qs = [_clean(q) for q in data.get("queries") or [] if isinstance(q, str) and 2 < len(q) < 80]
        dts = [_clean(t) for t in data.get("domain_terms") or [] if isinstance(t, str) and 1 < len(t) < 40]
        exc = [_clean(t) for t in data.get("exclude_title_terms") or [] if isinstance(t, str) and 1 < len(t) < 40]
        if qs:
            prof.queries = list(dict.fromkeys(qs + prof.queries))[:5]
        if dts:
            prof.domain_terms = list(dict.fromkeys(prof.domain_terms + dts))
        prof.exclude_terms = exc
        prof.used_llm = bool(qs or dts)
    return prof


# ── Relevance ──────────────────────────────────────────────────────────────

def _mentions(term: str, text: str, raw_text: str) -> bool:
    """Word-boundary match; ambiguous short skills need their acronym form."""
    t = _clean(term)
    if not t:
        return False
    if t in _CASE_SENSITIVE_SKILLS:
        return any(v in f" {raw_text} " for v in _CASE_SENSITIVE_SKILLS[t])
    pat = r"(?<![a-z0-9+#])" + re.escape(t) + r"(?![a-z0-9+#])"
    return re.search(pat, text) is not None


@dataclass
class _Scored:
    posting: ExternalPosting
    score: float
    title_hits: int
    core_hits: list[str]
    nice_hits: list[str]


def score_posting(p: ExternalPosting, prof: ResearchProfile) -> _Scored:
    raw = f"{p.title} {' '.join(p.skills)} {p.text}"
    low = raw.lower()
    title_low = (p.title or "").lower()
    title_hits = sum(1 for d in prof.domain_terms if _mentions(d, title_low, p.title or ""))
    core_hits = [s for s in prof.core_skills if _mentions(s, low, raw)]
    nice_hits = [s for s in prof.nice_skills if _mentions(s, low, raw)]
    score = 3.0 * min(title_hits, 2) + 2.0 * len(core_hits) + 0.5 * min(len(nice_hits), 6)
    if any(_mentions(x, title_low, p.title or "") for x in prof.exclude_terms):
        score = 0.0
    return _Scored(p, score, title_hits, core_hits, nice_hits)


def is_relevant(sc: _Scored, prof: ResearchProfile) -> bool:
    if sc.score <= 0:
        return False
    need_core = min(2, len(prof.core_skills))
    # Same domain in the title AND some of the required stack in the body, or
    # a body that clearly matches the required stack even with a different title.
    return (sc.title_hits >= 1 and len(sc.core_hits) >= need_core) or (
        len(sc.core_hits) >= max(2, (len(prof.core_skills) + 1) // 2)
    )


# ── Currency ───────────────────────────────────────────────────────────────
# Indicative rates to USD, used ONLY to compare postings priced in another
# currency. Snapshots record when a band was converted so the UI can say so.
_FX_TO_USD = {
    "USD": 1.0, "EUR": 1.08, "GBP": 1.27, "INR": 0.012, "AED": 0.272,
    "CAD": 0.73, "AUD": 0.66, "SGD": 0.74, "CHF": 1.12, "NZD": 0.60,
}
_CURRENCY_BY_COUNTRY = {
    "gb": "GBP", "uk": "GBP", "us": "USD", "in": "INR", "de": "EUR", "fr": "EUR",
    "nl": "EUR", "ie": "EUR", "es": "EUR", "it": "EUR", "ca": "CAD", "au": "AUD",
    "sg": "SGD", "ae": "AED", "nz": "NZD", "ch": "CHF",
}


def _convert(amount: int, src: str, dst: str) -> int | None:
    if src == dst:
        return amount
    a, b = _FX_TO_USD.get(src), _FX_TO_USD.get(dst)
    if not a or not b:
        return None
    return int(round(amount * a / b))


# ── Source adapters ────────────────────────────────────────────────────────
# Each takes (queries, profile) and returns ExternalPostings. The board-level
# keyword match is loose on purpose (max recall); `is_relevant` does precision.
SourceFetcher = Callable[[list[str], ResearchProfile], list[ExternalPosting]]


def _country_from_location(location: str | None) -> str | None:
    """Very light heuristic — turn a role location into a 2-letter country hint."""
    if not location:
        return None
    low = location.lower()
    for code, kws in {
        "gb": ("uk", "united kingdom", "london", "manchester", "birmingham", "leeds", "edinburgh"),
        "us": ("usa", "united states", "new york", "san francisco"),
        "in": ("india", "bengaluru", "bangalore", "mumbai", "delhi", "pune", "hyderabad", "chennai", "noida", "gurgaon"),
        "de": ("germany", "berlin", "munich"),
        "ca": ("canada", "toronto", "vancouver"),
        "ae": ("uae", "dubai", "abu dhabi"),
    }.items():
        if any(k in low for k in kws):
            return code
    return None


def _board_rows(rows: list[dict], source: str, currency: str, infer_country: bool) -> list[ExternalPosting]:
    out: list[ExternalPosting] = []
    for j in rows:
        lo, hi = _parse_salary_pair(j.get("salary_range"))
        tags = [normalize_skill(t) for t in (j.get("tags") or []) if t]
        loc = j.get("location") or ""
        out.append(
            ExternalPosting(
                title=j.get("title") or "",
                salary_min=lo,
                salary_max=hi,
                currency=currency,
                skills=[t for t in tags if t],
                source=source,
                text=(j.get("description") or "")[:4000],
                location=loc,
                country=_country_from_location(loc) if infer_country and "remote" not in loc.lower() else None,
            )
        )
    return out


def _fetch_remoteok(queries: list[str], prof: ResearchProfile) -> list[ExternalPosting]:
    """Free, keyless, remote-only (global USD salaries)."""
    rows = fetch_remoteok(queries, work_type="any", country=prof.country)
    return _board_rows(rows, "remoteok", "USD", infer_country=False)


def _fetch_arbeitnow(queries: list[str], prof: ResearchProfile) -> list[ExternalPosting]:
    """EU-leaning board; salaries often blank, still useful for demand."""
    rows = fetch_arbeitnow(queries, work_type="any", country=prof.country)
    return _board_rows(rows, "arbeitnow", "EUR", infer_country=True)


_ADZUNA_COUNTRIES = {"gb", "us", "in", "de", "fr", "nl", "ca", "au", "sg", "nz", "it", "es", "at", "be", "ch", "pl", "br", "mx", "za"}


def _fetch_adzuna(queries: list[str], prof: ResearchProfile) -> list[ExternalPosting]:
    """
    Local-market postings with salaries in local currency — the only source
    that reflects e.g. the Indian or UK market rather than global remote pay.
    Enabled when ADZUNA_APP_ID / ADZUNA_APP_KEY are set.
    """
    from app.core.config import settings
    from app.services.crawler.sources import _get, _strip_html
    import urllib.parse

    if not (settings.adzuna_app_id and settings.adzuna_app_key):
        return []
    country = (prof.country or "").lower()
    if country == "uk":
        country = "gb"
    if country not in _ADZUNA_COUNTRIES:
        return []
    currency = _CURRENCY_BY_COUNTRY.get(country, prof.currency)
    out: list[ExternalPosting] = []
    seen: set[str] = set()
    for q in queries[:3]:
        url = (
            f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
            f"?app_id={urllib.parse.quote(settings.adzuna_app_id)}"
            f"&app_key={urllib.parse.quote(settings.adzuna_app_key)}"
            f"&results_per_page=50&what={urllib.parse.quote(q)}&content-type=application/json"
        )
        try:
            data = _get(url)
        except Exception as exc:  # one bad query never kills the crawl
            logger.warning("adzuna market fetch failed q=%s: %s", q, exc)
            continue
        for j in (data or {}).get("results", []):
            jid = str(j.get("id", ""))
            if jid in seen:
                continue
            seen.add(jid)
            lo, hi = j.get("salary_min"), j.get("salary_max")
            out.append(
                ExternalPosting(
                    title=j.get("title") or "",
                    salary_min=int(lo) if lo else None,
                    salary_max=int(hi) if hi else None,
                    currency=currency,
                    source="adzuna",
                    text=_strip_html(j.get("description") or "")[:4000],
                    location=(j.get("location") or {}).get("display_name", ""),
                    country=country,
                )
            )
    return out


_SOURCES: dict[str, SourceFetcher] = {
    "adzuna": _fetch_adzuna,
    "remoteok": _fetch_remoteok,
    "arbeitnow": _fetch_arbeitnow,
}


# ── Aggregation ─────────────────────────────────────────────────────────────
MIN_SALARY_SAMPLES = 3


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


def _synthesise_from_own_pool(db: Session, role: Role | None, agency_id: int, prof: ResearchProfile):
    """
    Fallback when no relevant external postings exist: the agency's own roles
    in the same domain (title overlap) sketch a band. Marked "internal-only".
    """
    roles = db.query(Role).filter(Role.agency_id == agency_id).all()
    salaries: list[int] = []
    skills: dict[str, int] = {}
    titles: dict[str, int] = {}
    for r in roles:
        if role is not None and r.id == role.id:
            continue
        t = (r.title or "").lower()
        if not any(_mentions(d, t, r.title or "") for d in prof.domain_terms):
            continue
        cur = r.budget_currency or prof.currency
        for v in (r.salary_min, r.salary_max):
            if v:
                c = _convert(v, cur, prof.currency)
                if c:
                    salaries.append(c)
        for s in (r.required_skills or []) + (r.preferred_skills or []):
            skills[s] = skills.get(s, 0) + 1
        titles[r.title] = titles.get(r.title, 0) + 1
    return salaries, _top_n(skills, 10), _top_n(titles, 5)


def _salary_points(p: ExternalPosting) -> list[int]:
    return [v for v in (p.salary_min, p.salary_max) if v]


def crawl_role_market(
    db: Session,
    agency_id: int,
    *,
    role: Role | None = None,
    query: str | None = None,
    location: str | None = None,
    sources: Iterable[str] | None = None,
    use_llm: bool = True,
) -> MarketSnapshot:
    """Research the role's market, keep only comparable postings, persist a snapshot."""
    if role is None and not query:
        raise ValueError("crawl_role_market requires a query or a role")
    if role is None:
        role = Role(title=query, location=location, required_skills=[], preferred_skills=[], budget_currency="USD")
        role_for_link = None
    else:
        role_for_link = role
    prof = build_research_profile(role, use_llm=use_llm)
    if query and query not in prof.queries:
        prof.queries.insert(0, query)
    location = location or role.location

    scanned: list[ExternalPosting] = []
    used_sources: list[str] = []
    selected = list(sources) if sources is not None else list(_SOURCES.keys())
    for name in selected:
        fetcher = _SOURCES.get(name)
        if not fetcher:
            continue
        try:
            batch = fetcher(prof.queries, prof)
        except Exception:  # pragma: no cover — never let one source crash the crawl
            logger.exception("market source %s failed", name)
            continue
        scanned.extend(batch)

    # Precision pass + dedupe (same title at same location from two boards).
    relevant: list[_Scored] = []
    seen: set[tuple[str, str]] = set()
    for p in scanned:
        key = (_clean(p.title), _clean(p.location))
        if key in seen:
            continue
        sc = score_posting(p, prof)
        if is_relevant(sc, prof):
            seen.add(key)
            relevant.append(sc)
            if p.source not in used_sources:
                used_sources.append(p.source)
    relevant.sort(key=lambda s: -s.score)
    postings = [s.posting for s in relevant]

    # Salary: prefer postings from the role's own market in its own currency;
    # only fall back to converted global/remote pay, and say so.
    local = [p for p in postings if prof.country and p.country == prof.country and _salary_points(p)]
    salary_basis = None
    salary_note = None
    salaries: list[int] = []
    paid: list[ExternalPosting] = []
    if len(local) >= MIN_SALARY_SAMPLES:
        paid = local
        for p in local:
            for v in _salary_points(p):
                c = _convert(v, p.currency, prof.currency)
                if c:
                    salaries.append(c)
        salary_basis = "local"
    else:
        foreign_ccy: set[str] = set()
        paid = [p for p in postings if _salary_points(p)]
        for p in paid:
            for v in _salary_points(p):
                c = _convert(v, p.currency, prof.currency)
                if c:
                    salaries.append(c)
                    if p.currency != prof.currency:
                        foreign_ccy.add(p.currency)
        if salaries:
            salary_basis = "global"
            salary_note = (
                "Based on global/remote postings"
                + (f" converted from {', '.join(sorted(foreign_ccy))} at indicative rates" if foreign_ccy else "")
                + " — not the local market."
            )
    salary_postings = len(paid)
    if salaries and salary_postings < MIN_SALARY_SAMPLES:
        salary_note = f"Only {salary_postings} comparable posting(s) listed pay — too few for a band."
        salaries = []
        salary_basis = None

    # Skill demand: share of comparable postings mentioning each role skill,
    # plus other recurring skill tags the role doesn't list yet.
    demand: list[dict] = []
    n = len(relevant)
    if n:
        for s in prof.core_skills + prof.nice_skills:
            hits = sum(1 for sc in relevant if s in sc.core_hits or s in sc.nice_hits)
            if hits:
                demand.append({"skill": s, "pct": round(100 * hits / n), "required": s in prof.core_skills})
        demand.sort(key=lambda d: (-d["pct"], not d["required"], d["skill"]))
    role_skills = set(prof.core_skills) | set(prof.nice_skills)
    extra: dict[str, int] = {}
    for p in postings:
        for t in set(p.skills):
            t = _clean(t)
            if not t or t in role_skills or t in _NON_SKILL_TAGS or len(t) < 2:
                continue
            extra[t] = extra.get(t, 0) + 1
    emerging = [k for k in _top_n(extra, 8) if extra[k] >= 2]

    top_skills = [d["skill"] for d in demand[:10]] + emerging
    title_counter: dict[str, int] = {}
    for p in postings:
        if p.title:
            title_counter[p.title] = title_counter.get(p.title, 0) + 1
    competing = _top_n(title_counter, 6)

    if not postings:
        salaries, top_skills, competing = _synthesise_from_own_pool(db, role_for_link, agency_id, prof)
        used_sources = ["internal-only"] if (salaries or top_skills) else []
        salary_basis = "internal" if salaries else None
        salary_note = (
            "No comparable external postings found — band from your agency's own roles in this domain."
            if salaries else "No comparable postings found for this role's domain."
        )

    p25, p50, p75 = _percentiles(salaries)

    snap = MarketSnapshot(
        agency_id=agency_id,
        role_id=role_for_link.id if role_for_link else None,
        query=" | ".join(prof.queries)[:300],
        location=location,
        sample_size=len(postings),
        salary_p25=p25,
        salary_p50=p50,
        salary_p75=p75,
        currency=prof.currency,
        top_skills=top_skills or None,
        competing_roles=competing or None,
        sources=used_sources or None,
    )
    db.add(snap)

    if role_for_link is not None:
        # Cache the aggregate on the role too, so the client-shareable draft
        # view doesn't have to join across snapshots.
        role_for_link.market_snapshot = {
            "sample_size": snap.sample_size,
            "scanned": len(scanned),
            "salary_p25": snap.salary_p25,
            "salary_p50": snap.salary_p50,
            "salary_p75": snap.salary_p75,
            "salary_samples": len(salaries),
            "salary_basis": salary_basis,
            "salary_note": salary_note,
            "currency": snap.currency,
            "top_skills": snap.top_skills,
            "skill_demand": demand[:15] or None,
            "emerging_skills": emerging or None,
            "competing_roles": snap.competing_roles,
            "sources": snap.sources,
            "queries": prof.queries,
            "domain_terms": prof.domain_terms[:12],
            "market": (prof.country or "").upper() or None,
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

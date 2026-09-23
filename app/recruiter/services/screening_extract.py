"""
Deterministic screening-field extraction from recruiter free text.

Runs with or without an LLM. Every value it returns is lifted from the
recruiter's own words (numbers, dates, keywords) — it never guesses. The
caller merges it with the LLM extractor and only fills fields that are
still empty, so a recruiter-entered value always wins.

Handles the phrasings recruiters actually type, e.g.:
  "Salary: Currently on 30LPA, expecting a competitive raise."
  "Expecting 35 LPA" · "wants £95k" · "looking for 20% hike"
  "Notice Period: Last working day is 6th December ... early release by
   25th October which is not confirmed yet."
  "2 months notice" · "immediate joiner" · "prefers hybrid" · "open to relocate"
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"),
            ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
            ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"),
            ("nov", "november"), ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}
_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
# "6th December", "6 Dec 2026", "December 6th", "Dec 6"
_DATE_RE = re.compile(
    rf"(?:(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH_RE}\.?(?:,?\s+(\d{{4}}))?"
    rf"|{_MONTH_RE}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?)",
    re.I,
)

_CURRENCY_BY_COUNTRY = {
    "IN": "INR", "GB": "GBP", "UK": "GBP", "US": "USD", "AE": "AED", "CA": "CAD",
    "AU": "AUD", "SG": "SGD", "DE": "EUR", "FR": "EUR", "NL": "EUR", "IE": "EUR",
}
_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR", "₹": "INR"}

# A money token: optional symbol/code, number, optional unit (LPA, lakh, k, cr).
_MONEY_RE = re.compile(
    r"(?P<sym>[£$€₹]|\b(?:inr|rs\.?|gbp|usd|eur|aed|cad|aud|sgd)\b)?\s*"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>lpa|l\.p\.a|lakhs?\s*(?:per\s+annum|p\.?a\.?)?|lacs?|k\b|cr(?:ore)?s?)?",
    re.I,
)


def _parse_date(m: re.Match, ref: date) -> date | None:
    day = m.group(1) or m.group(5)
    mon = m.group(2) or m.group(4)
    year = m.group(3) or m.group(6)
    try:
        d = int(day)
        mo = _MONTHS[mon.lower().rstrip(".")[:4] if mon.lower().startswith("sept") else mon.lower()[:3]]
    except (KeyError, TypeError, ValueError):
        return None
    y = int(year) if year else ref.year
    try:
        out = date(y, mo, d)
    except ValueError:
        return None
    # No explicit year and the date already passed → next year.
    if not year and out < ref - timedelta(days=14):
        try:
            out = date(y + 1, mo, d)
        except ValueError:
            return None
    return out


def _money(m: re.Match, default_currency: str | None) -> dict[str, Any] | None:
    raw = m.group("num").replace(",", "")
    try:
        val = float(raw)
    except ValueError:
        return None
    unit = (m.group("unit") or "").lower().replace(" ", "")
    sym = (m.group("sym") or "").lower().rstrip(".")
    currency = _SYMBOLS.get(sym) or {"inr": "INR", "rs": "INR", "gbp": "GBP", "usd": "USD",
                                     "eur": "EUR", "aed": "AED", "cad": "CAD", "aud": "AUD",
                                     "sgd": "SGD"}.get(sym)
    if unit.startswith(("lpa", "l.p.a", "lakh", "lac")):
        amount = val * 100_000
        currency = currency or "INR"
    elif unit.startswith("cr"):
        amount = val * 10_000_000
        currency = currency or "INR"
    elif unit == "k":
        amount = val * 1_000
    else:
        amount = val
        # Bare small numbers ("30") with no unit and no symbol are ambiguous.
        if not sym and amount < 1000:
            return None
    currency = currency or default_currency
    return {"amount": int(round(amount)), "currency": currency, "period": "YEAR"}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


_COMP_HINT = re.compile(r"\b(salary|ctc|comp(?:ensation)?|package|pay|lpa|expect|current(?:ly)?|drawing|earning|rate|hike|raise|increment)\b|[£$€₹]", re.I)
_CURRENT_HINT = re.compile(r"\b(current(?:ly)?|drawing|earning|existing|present(?:ly)?|now on|at present)\b", re.I)
_EXPECT_HINT = re.compile(r"\b(expect(?:s|ing|ed|ation)?|wants?|looking for|asking|target(?:ing)?|desired?|aspir\w*)\b", re.I)
_COMPETITIVE = re.compile(r"\b(competitive|market[- ]rate|negotiable|open to discuss\w*|as per (?:industry|market)|best in (?:the )?industry)\b", re.I)
_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|percent|per cent)\s*(?:hike|raise|increase|increment|uplift|jump|more)?", re.I)


def _extract_comp(text: str, default_currency: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    current = expected = None
    competitive = False
    uplift = None
    note = None
    for s in _sentences(text):
        if not _COMP_HINT.search(s):
            continue
        # Split a sentence like "Currently on 30LPA, expecting a competitive raise"
        # into clauses so current/expected don't bleed into each other.
        for clause in re.split(r"[,;]|\band\b|\bbut\b|(?=\b(?:expect\w*|wants?|looking for|asking|target\w*)\b)", s, flags=re.I):
            c = clause.strip()
            if not c:
                continue
            money = None
            for m in _MONEY_RE.finditer(c):
                money = _money(m, default_currency)
                if money:
                    break
            pct = _PCT.search(c)
            is_expect = bool(_EXPECT_HINT.search(c))
            is_current = bool(_CURRENT_HINT.search(c)) and not is_expect
            if _COMPETITIVE.search(c) and (is_expect or re.search(r"raise|hike|salary|package|comp", c, re.I)):
                competitive = True
                note = note or c
            if pct and (is_expect or re.search(r"hike|raise|increase|increment|uplift", c, re.I)):
                uplift = float(pct.group(1))
                note = note or c
            if money:
                if is_current and current is None:
                    current = money
                elif is_expect and expected is None:
                    expected = money
                elif current is None and expected is None and re.search(r"\b(ctc|salary)\b", c, re.I) and not is_expect:
                    current = money
    if current:
        out["current_compensation"] = current
    if expected:
        out["expected_compensation"] = {**expected, "basis": "fixed"}
    elif competitive or uplift is not None:
        cur = current or {}
        exp: dict[str, Any] = {
            "basis": "competitive",
            "currency": cur.get("currency") or default_currency,
            "period": cur.get("period") or "YEAR",
        }
        if uplift is not None:
            exp["uplift_pct"] = uplift
            if cur.get("amount"):
                exp["target"] = int(round(cur["amount"] * (1 + uplift / 100)))
                exp["estimate_source"] = "uplift"
        if note:
            exp["note"] = note[:500]
        out["expected_compensation"] = exp
    return out


_NOTICE_RE = re.compile(
    r"(\d{1,3})\s*(?:-|to)?\s*(day|week|month)s?\b[^.]{0,20}?\bnotice"
    r"|notice(?:\s+period)?[^.\d]{0,25}?(\d{1,3})\s*(day|week|month)s?",
    re.I,
)
_LWD_RE = re.compile(r"\b(last\s+working\s+day|lwd|relieving\s+date|serving\s+notice\s+(?:till|until))\b", re.I)
_EARLY_RE = re.compile(r"\b(early\s+release|buy[- ]?out|release(?:d)?\s+(?:by|on|early))\b", re.I)
_UNCONFIRMED = re.compile(r"\b(not\s+(?:yet\s+)?confirmed|unconfirmed|tentative|trying|negotiat\w*|if\s+possible|may\s+be)\b", re.I)
_IMMEDIATE = re.compile(r"\b(immediate(?:ly)?\s+(?:joiner|available|start)|immediate joining|can join immediately|available immediately|serving\s+no\s+notice|no notice)\b", re.I)


def _extract_notice(text: str, ref: date) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if _IMMEDIATE.search(text):
        out["availability_immediate"] = True
        return out
    m = _NOTICE_RE.search(text)
    notice: dict[str, Any] | None = None
    if m:
        val = m.group(1) or m.group(3)
        unit = (m.group(2) or m.group(4)).upper()
        notice = {"value": int(val), "unit": unit, "negotiable": False}

    lwd = early = None
    early_unconfirmed = False
    for s in _sentences(text):
        for clause in re.split(r"[;]|\.\s", s):
            dm = list(_DATE_RE.finditer(clause))
            if not dm:
                continue
            if _LWD_RE.search(clause) and lwd is None:
                # First date after the LWD phrase.
                pos = _LWD_RE.search(clause).end()
                after = [d for d in dm if d.start() >= pos] or dm
                lwd = _parse_date(after[0], ref)
                # Same clause may carry the early-release date too.
                em = _EARLY_RE.search(clause)
                if em:
                    later = [d for d in dm if d.start() >= em.end()]
                    if later:
                        early = _parse_date(later[0], ref)
                        early_unconfirmed = bool(_UNCONFIRMED.search(clause[em.start():]))
            elif _EARLY_RE.search(clause) and early is None:
                em = _EARLY_RE.search(clause)
                later = [d for d in dm if d.start() >= em.end()] or dm
                early = _parse_date(later[0], ref)
                early_unconfirmed = bool(_UNCONFIRMED.search(clause))
    if lwd:
        # Available the day after their last working day.
        out["availability_date"] = (lwd + timedelta(days=1)).isoformat()
        remaining = max(0, (lwd - ref).days)
        if notice is None:
            notice = {"value": remaining, "unit": "DAY", "negotiable": early is not None}
        notice["available_from"] = (lwd + timedelta(days=1)).isoformat()
        out["last_working_day"] = lwd.isoformat()
    if early:
        out["early_release_date"] = early.isoformat()
        out["early_release_confirmed"] = not early_unconfirmed
        if notice is not None:
            notice["negotiable"] = True
        if not early_unconfirmed:
            out["availability_date"] = (early + timedelta(days=1)).isoformat()
    if notice is not None:
        out["notice_period"] = notice
    return out


_WORK_MODEL = [
    ("remote", re.compile(r"\b(fully\s+remote|remote(?:\s+only|\s+pref\w*)?|wfh|work from home)\b", re.I)),
    ("hybrid", re.compile(r"\bhybrid\b", re.I)),
    ("onsite", re.compile(r"\b(on[- ]?site|in[- ]office|work from office|wfo)\b", re.I)),
    ("flexible", re.compile(r"\b(flexible (?:on|with) (?:work|location|model)|open to (?:any|all) (?:work )?models?)\b", re.I)),
]
_RELOC_YES = re.compile(r"\b(willing|open|happy|ready|okay|ok)\s+to\s+relocat\w*", re.I)
_RELOC_NO = re.compile(r"\b(not\s+(?:willing|open)\s+to\s+relocat\w*|won'?t\s+relocat\w*|no\s+relocation|cannot\s+relocat\w*)", re.I)
_RELOC_COND = re.compile(r"\brelocat\w*[^.]{0,40}\b(if|provided|depending|subject to)\b", re.I)


def _extract_prefs(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for model, rx in _WORK_MODEL:
        if rx.search(text):
            out["preferred_work_model"] = model
            break
    if _RELOC_NO.search(text):
        out["relocation"] = "no"
    elif _RELOC_COND.search(text):
        out["relocation"] = "conditional"
    elif _RELOC_YES.search(text):
        out["relocation"] = "yes"
    return out


def heuristic_extract(
    text: str, country_code: str | None = None, ref: date | None = None
) -> dict[str, Any]:
    """Return only the fields actually found in `text` (no nulls)."""
    text = (text or "").strip()
    if not text:
        return {}
    ref = ref or date.today()
    default_currency = _CURRENCY_BY_COUNTRY.get((country_code or "").upper())
    out: dict[str, Any] = {}
    out.update(_extract_comp(text, default_currency))
    out.update(_extract_notice(text, ref))
    out.update(_extract_prefs(text))
    return out

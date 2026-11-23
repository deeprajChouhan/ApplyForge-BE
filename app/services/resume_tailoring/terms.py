"""
Deterministic ATS term matching — no LLM, fully testable.

Rules (what a literal-match ATS sees):
  • Case-insensitive, word-boundary aware: "Java" does not match "JavaScript",
    "Go" does not match "Google", "C++" and "Node.js" work.
  • Hyphen / space / no-space variants of multi-word terms are treated alike
    ("front-end" == "front end"); a trailing plural "s" is tolerated.
  • Alias spellings (aliases.py) are tracked separately from exact matches:
    exact = an ATS keyword filter would find the JD's phrase,
    alias = the candidate has the thing under another name (evidence only).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from app.services.resume_tailoring.aliases import ALIAS_INDEX, AMBIGUOUS_SHORT

IMPORTANCE_WEIGHT = {"required": 3, "keyword": 2, "preferred": 1}


def norm(s: str | None) -> str:
    s = str(s or "").lower()
    s = s.replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-")
    s = s.replace("’", "'")
    return " ".join(s.split())


@lru_cache(maxsize=4096)
def term_pattern(term: str) -> re.Pattern | None:
    t = norm(term).strip(" .,;:")
    if not t:
        return None
    parts = re.split(r"[\s\-]+", t)
    escaped = [re.escape(p) for p in parts if p]
    if not escaped:
        return None
    body = r"[\s\-]?".join(escaped)
    # Tolerate a simple plural on alphabetic endings ("api" ~ "apis").
    if re.search(r"[a-z]$", t) and len(t) > 2:
        body += r"(?:s|es)?"
    return re.compile(rf"(?<![a-z0-9+#]){body}(?![a-z0-9+#])", re.IGNORECASE)


def count_term(text: str, term: str) -> int:
    pat = term_pattern(term)
    if pat is None or not text:
        return 0
    return len(pat.findall(norm(text)))


def has_term(text: str, term: str) -> bool:
    return count_term(text, term) > 0


def aliases_for(term: str) -> list[str]:
    """Equivalent spellings for evidence search (excludes the term itself and ambiguous shorts)."""
    t = norm(term)
    group = ALIAS_INDEX.get(t, frozenset())
    return sorted(a for a in group if a != t and a not in AMBIGUOUS_SHORT)


def find_alias(text: str, term: str, extra_variants: list[str] | None = None) -> str | None:
    """Return the first alias / variant spelling of `term` present in text, if any."""
    candidates = aliases_for(term)
    for v in extra_variants or []:
        nv = norm(v)
        if nv and nv != norm(term) and nv not in AMBIGUOUS_SHORT and nv not in candidates:
            candidates.append(nv)
    for a in candidates:
        if has_term(text, a):
            return a
    return None


def split_bullets(description: str | None) -> list[str]:
    """Split a free-text description into bullet lines."""
    if not description:
        return []
    raw = str(description).replace("\r", "\n")
    lines = re.split(r"\n+|(?:^|\s)[•▪●◦‣∙]\s+", raw)
    out = []
    for ln in lines:
        ln = re.sub(r"^\s*(?:[-*•▪●◦‣∙]|\d+[.)])\s+", "", ln).strip()
        if ln:
            out.append(ln)
    return out


_NUM_RE = re.compile(r"(?<![A-Za-z])(?:[$£€])?\d[\d,]*(?:\.\d+)?\s*(?:%|k|m|bn|x|\+)?", re.IGNORECASE)


def numbers_in(text: str) -> set[str]:
    """Numeric claims in a piece of text, normalised ('1,200' -> '1200', '32 %' -> '32%')."""
    out: set[str] = set()
    for m in _NUM_RE.findall(text or ""):
        n = m.lower().replace(",", "").replace(" ", "").lstrip("$£€")
        n = n.rstrip("+")
        if not n or not any(c.isdigit() for c in n):
            continue
        out.add(n)
    return out


def _num_core(n: str) -> str:
    return re.sub(r"[^\d.]", "", n)


def unsupported_numbers(generated: str, source_corpus: str) -> list[str]:
    """Numbers in generated text that do not appear anywhere in the source."""
    src = numbers_in(source_corpus)
    src_cores = {_num_core(n) for n in src}
    bad = []
    for n in numbers_in(generated):
        core = _num_core(n)
        if n in src or core in src_cores:
            continue
        # Years in date ranges are fine only if the year itself is in source (covered above).
        bad.append(n)
    return sorted(bad)


def quote_in_source(quote: str, corpus: str, threshold: float = 0.8) -> bool:
    """True if `quote` is (nearly) verbatim in the corpus — guards against invented evidence."""
    q = norm(quote).strip(" .\"'")
    if len(q) < 3:
        return False
    c = norm(corpus)
    if q in c:
        return True
    q_tokens = re.findall(r"[a-z0-9+#.]+", q)
    if len(q_tokens) < 3:
        return False
    c_tokens = set(re.findall(r"[a-z0-9+#.]+", c))
    hit = sum(1 for t in q_tokens if t in c_tokens)
    return hit / len(q_tokens) >= threshold


@dataclass
class Term:
    term: str
    importance: str = "keyword"           # required | keyword | preferred
    variants: list[str] = field(default_factory=list)

    @property
    def weight(self) -> int:
        return IMPORTANCE_WEIGHT.get(self.importance, 1)


def coverage(terms: list[Term], text: str) -> dict:
    """Weighted exact-match coverage of terms in text (what a literal ATS filter sees)."""
    total_w = sum(t.weight for t in terms) or 0
    hit_w = sum(t.weight for t in terms if has_term(text, t.term))
    req = [t for t in terms if t.importance == "required"]
    req_hit = sum(1 for t in req if has_term(text, t.term))
    return {
        "weighted_pct": round(100 * hit_w / total_w) if total_w else 0,
        "matched": sum(1 for t in terms if has_term(text, t.term)),
        "total": len(terms),
        "required_matched": req_hit,
        "required_total": len(req),
    }

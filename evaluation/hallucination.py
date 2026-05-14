"""
evaluation/hallucination.py
---------------------------
Hallucination detection for generated job-application documents.

Strategy (in order):
  1. Regex + string-matching (fast, no API cost)
  2. LLM fallback (only when structured extraction is needed / regex ambiguous)

A "hallucination" is a factual claim in the generated text that cannot be
grounded in the source CV / profile text provided.

Detects:
  • Years-of-experience claims          ("5+ years", "over a decade")
  • Skill mentions                      ("proficient in Kubernetes")
  • Job titles / company names          ("Senior Engineer at Google")
  • Quantified achievements             ("reduced costs by 40%")
  • Degree / institution claims         ("BSc Computer Science at MIT")
  • Certification claims                ("AWS Certified Solutions Architect")

Usage (standalone):
    from evaluation.hallucination import HallucinationChecker
    checker = HallucinationChecker()
    flags = checker.check(generated_text="...", source_cv="...")
    for f in flags:
        print(f)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class HallucinationFlag:
    claim: str                    # The suspicious text snippet
    claim_type: str               # years_experience | skill | title | achievement | education | certification
    reason: str                   # Why it looks hallucinated
    confidence: float             # 0.0–1.0 (how confident we are it's a hallucination)
    context: str = ""             # Surrounding sentence for review

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "claim_type": self.claim_type,
            "reason": self.reason,
            "confidence": self.confidence,
            "context": self.context,
        }


# ── Regex patterns ────────────────────────────────────────────────────────────

# Years of experience patterns
_YOE_PATTERNS = [
    re.compile(
        r"(\d+)\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp\.?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:over|more than|nearly|almost|around)\s+(\d+)\s*(?:years?|yrs?)",
        re.IGNORECASE,
    ),
    re.compile(r"(\d+)-year(?:\s+career|\s+experience)?", re.IGNORECASE),
    re.compile(r"a\s+decade|two\s+decades|several\s+years", re.IGNORECASE),
]

# Quantified achievements
_ACHIEVEMENT_PATTERN = re.compile(
    r"(?:increased|decreased|reduced|improved|grew|saved|delivered|generated|"
    r"achieved|boosted|cut|drove|led|managed|handled|processed|served|built|"
    r"deployed|launched|migrated|scaled|optimised|optimized)\w*"
    r"(?:[^.]{0,80})"
    r"(?:by\s+\d+\s*%|\d+\s*[xX]|\$\s*[\d,]+(?:k|m|b)?|\d+(?:k|K|M|B)?\s*(?:users?|customers?|requests?|calls?|records?))",
    re.IGNORECASE,
)

# Certification patterns
_CERT_PATTERN = re.compile(
    r"(?:AWS|GCP|Azure|Google|Cisco|PMI|PMP|CCNA|CPA|CFA|CISSP|"
    r"certified|certification|certificate)\s+(?:\w+\s*){1,5}",
    re.IGNORECASE,
)

# Degree + institution
_DEGREE_PATTERN = re.compile(
    r"(?:BSc|BEng|BA|MSc|MEng|MBA|PhD|B\.S\.|M\.S\.|B\.Eng|M\.Eng)\b"
    r"(?:[^.]{0,60})",
    re.IGNORECASE,
)

# Job title at company
_TITLE_PATTERN = re.compile(
    r"(?:as\s+(?:a|an)\s+|worked\s+as\s+(?:a|an)\s+)"
    r"((?:[A-Z][a-z]+\s*){1,4})"
    r"(?:\s+at\s+([A-Z][A-Za-z0-9\s&,\.]+))?",
)


# ── Helper utilities ──────────────────────────────────────────────────────────

def _sentences(text: str) -> list[str]:
    """Split text into sentences."""
    return re.split(r"(?<=[.!?])\s+", text.strip())


def _normalise(text: str) -> str:
    """Lowercase + collapse whitespace."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _text_contains(haystack: str, needle: str, threshold: float = 0.8) -> bool:
    """
    Returns True if needle (or enough of its words) appear in haystack.
    Uses simple token-overlap for fuzzy matching without external libraries.
    """
    needle_l = _normalise(needle)
    haystack_l = _normalise(haystack)

    # Direct substring match
    if needle_l in haystack_l:
        return True

    # Token overlap
    n_tokens = set(re.findall(r"\b\w{3,}\b", needle_l))
    h_tokens = set(re.findall(r"\b\w{3,}\b", haystack_l))
    if not n_tokens:
        return False
    overlap = len(n_tokens & h_tokens) / len(n_tokens)
    return overlap >= threshold


def _extract_years_from_cv(cv_text: str) -> set[int]:
    """Extract all standalone year numbers from CV (for cross-referencing)."""
    matches = re.findall(r"\b(19[89]\d|20[0-3]\d)\b", cv_text)
    return {int(m) for m in matches}


def _compute_cv_yoe(cv_text: str) -> Optional[float]:
    """
    Estimate total years of experience from CV date ranges.
    Returns None if no date ranges found.
    """
    date_pairs = re.findall(
        r"\b((?:19|20)\d{2})\b.{0,10}\b((?:19|20)\d{2}|[Pp]resent|[Cc]urrent)\b",
        cv_text,
    )
    if not date_pairs:
        return None

    import datetime
    current_year = datetime.datetime.utcnow().year
    total_years = 0.0
    for start_s, end_s in date_pairs:
        try:
            start_y = int(start_s)
            end_y = current_year if end_s.lower() in ("present", "current") else int(end_s)
            if end_y >= start_y:
                total_years = max(total_years, end_y - start_y)
        except ValueError:
            continue
    return total_years if total_years > 0 else None


# ── LLM fallback prompt ───────────────────────────────────────────────────────

_LLM_HALLUCINATION_SYSTEM = """\
You are a factual-accuracy auditor for AI-generated job-application documents.

Given a generated document and the original source CV, identify any factual claims
in the generated document that CANNOT be verified from the source CV.

Focus on:
- Years of experience claims
- Specific skills or technologies
- Job titles, companies, or roles
- Quantified achievements (percentages, numbers, dollar figures)
- Degrees, institutions, certifications

Return ONLY a valid JSON array. Each item:
{
  "claim": "<exact text excerpt>",
  "claim_type": "years_experience|skill|title|achievement|education|certification",
  "reason": "<why this cannot be verified>",
  "confidence": <0.0-1.0>
}
Return [] if no hallucinations are found.
"""

_LLM_HALLUCINATION_USER = """\
=== SOURCE CV ===
{cv_text}

=== GENERATED DOCUMENT ===
{generated_text}

List all claims in the generated document that are NOT supported by the source CV.
"""


# ── Main checker ──────────────────────────────────────────────────────────────

class HallucinationChecker:
    """
    Detect hallucinated factual claims in generated job-application documents.

    Works purely with regex+heuristics when no LLM is provided.
    When an LLM is available it's used as a fallback for ambiguous cases.
    """

    def __init__(self, llm_provider: Optional[Any] = None) -> None:
        self._llm = llm_provider

    # ── Public API ────────────────────────────────────────────────────────────

    def check(
        self,
        generated_text: str,
        source_cv: str,
        use_llm_fallback: bool = True,
    ) -> list[HallucinationFlag]:
        """
        Run hallucination detection.

        Args:
            generated_text: The AI-generated document to audit.
            source_cv: The candidate's original CV text used as ground truth.
            use_llm_fallback: If True and an LLM provider is set, ambiguous
                              claims are passed to the LLM for a second opinion.

        Returns:
            List of HallucinationFlag instances (may be empty if no issues found).
        """
        flags: list[HallucinationFlag] = []

        flags.extend(self._check_years_of_experience(generated_text, source_cv))
        flags.extend(self._check_achievements(generated_text, source_cv))
        flags.extend(self._check_certifications(generated_text, source_cv))
        flags.extend(self._check_degrees(generated_text, source_cv))
        flags.extend(self._check_titles(generated_text, source_cv))

        # LLM fallback: run when regex found high-confidence flags OR unconditionally
        if use_llm_fallback and self._llm is not None:
            llm_flags = self._llm_check(generated_text, source_cv)
            # Merge: only add LLM flags not already caught by regex
            existing_claims = {f.claim.lower()[:60] for f in flags}
            for lf in llm_flags:
                if lf.claim.lower()[:60] not in existing_claims:
                    flags.append(lf)

        # Deduplicate by claim prefix
        seen: set[str] = set()
        unique: list[HallucinationFlag] = []
        for f in flags:
            key = f.claim.lower()[:60]
            if key not in seen:
                seen.add(key)
                unique.append(f)

        return sorted(unique, key=lambda f: -f.confidence)

    def check_as_dicts(
        self,
        generated_text: str,
        source_cv: str,
        use_llm_fallback: bool = True,
    ) -> list[dict[str, Any]]:
        """Convenience wrapper returning serialisable dicts."""
        return [f.to_dict() for f in self.check(generated_text, source_cv, use_llm_fallback)]

    # ── Individual claim checkers ─────────────────────────────────────────────

    def _check_years_of_experience(
        self, generated_text: str, source_cv: str
    ) -> list[HallucinationFlag]:
        flags: list[HallucinationFlag] = []
        cv_yoe = _compute_cv_yoe(source_cv)

        for sentence in _sentences(generated_text):
            for pattern in _YOE_PATTERNS:
                for match in pattern.finditer(sentence):
                    snippet = match.group(0)
                    # Try to extract the claimed number
                    nums = re.findall(r"\d+", snippet)
                    if not nums:
                        # Qualitative claim ("a decade", etc.)
                        if not _text_contains(source_cv, snippet):
                            flags.append(HallucinationFlag(
                                claim=snippet,
                                claim_type="years_experience",
                                reason="Qualitative experience claim not grounded in CV dates.",
                                confidence=0.55,
                                context=sentence[:200],
                            ))
                        continue
                    claimed = int(nums[0])
                    if cv_yoe is not None and claimed > cv_yoe + 2:
                        flags.append(HallucinationFlag(
                            claim=snippet,
                            claim_type="years_experience",
                            reason=(
                                f"Generated document claims {claimed} years experience but CV "
                                f"date ranges imply at most ~{cv_yoe:.0f} years."
                            ),
                            confidence=0.80,
                            context=sentence[:200],
                        ))
                    elif cv_yoe is None and not _text_contains(source_cv, str(claimed)):
                        flags.append(HallucinationFlag(
                            claim=snippet,
                            claim_type="years_experience",
                            reason=f"Claimed {claimed} years not corroborated by CV text.",
                            confidence=0.50,
                            context=sentence[:200],
                        ))
        return flags

    def _check_achievements(
        self, generated_text: str, source_cv: str
    ) -> list[HallucinationFlag]:
        flags: list[HallucinationFlag] = []
        for sentence in _sentences(generated_text):
            for match in _ACHIEVEMENT_PATTERN.finditer(sentence):
                snippet = match.group(0)
                # Extract the metric
                metric_match = re.search(
                    r"\d+\s*%|\$\s*[\d,]+(?:k|m|b)?|\d+(?:k|K|M|B)?\s+\w+",
                    snippet,
                    re.IGNORECASE,
                )
                if metric_match:
                    metric = metric_match.group(0)
                    if not _text_contains(source_cv, metric, threshold=0.9):
                        flags.append(HallucinationFlag(
                            claim=snippet.strip(),
                            claim_type="achievement",
                            reason=(
                                f"Quantified achievement metric '{metric}' not found in source CV."
                            ),
                            confidence=0.75,
                            context=sentence[:200],
                        ))
        return flags

    def _check_certifications(
        self, generated_text: str, source_cv: str
    ) -> list[HallucinationFlag]:
        flags: list[HallucinationFlag] = []
        for sentence in _sentences(generated_text):
            for match in _CERT_PATTERN.finditer(sentence):
                snippet = match.group(0).strip()
                if len(snippet) < 5:
                    continue
                if not _text_contains(source_cv, snippet, threshold=0.6):
                    flags.append(HallucinationFlag(
                        claim=snippet,
                        claim_type="certification",
                        reason="Certification claim not found in source CV.",
                        confidence=0.70,
                        context=sentence[:200],
                    ))
        return flags

    def _check_degrees(
        self, generated_text: str, source_cv: str
    ) -> list[HallucinationFlag]:
        flags: list[HallucinationFlag] = []
        for sentence in _sentences(generated_text):
            for match in _DEGREE_PATTERN.finditer(sentence):
                snippet = match.group(0).strip()
                if not _text_contains(source_cv, snippet, threshold=0.5):
                    flags.append(HallucinationFlag(
                        claim=snippet,
                        claim_type="education",
                        reason="Degree/institution claim not verifiable in source CV.",
                        confidence=0.65,
                        context=sentence[:200],
                    ))
        return flags

    def _check_titles(
        self, generated_text: str, source_cv: str
    ) -> list[HallucinationFlag]:
        flags: list[HallucinationFlag] = []
        for sentence in _sentences(generated_text):
            for match in _TITLE_PATTERN.finditer(sentence):
                title = match.group(1).strip()
                company = match.group(2).strip() if match.group(2) else ""
                claim = f"{title}{' at ' + company if company else ''}".strip()
                if len(claim) < 4:
                    continue
                check_str = f"{title} {company}".strip()
                if not _text_contains(source_cv, check_str, threshold=0.6):
                    flags.append(HallucinationFlag(
                        claim=claim,
                        claim_type="title",
                        reason="Job title / company claim not found in source CV.",
                        confidence=0.55,
                        context=sentence[:200],
                    ))
        return flags

    # ── LLM fallback ──────────────────────────────────────────────────────────

    def _llm_check(
        self, generated_text: str, source_cv: str
    ) -> list[HallucinationFlag]:
        try:
            user_prompt = _LLM_HALLUCINATION_USER.format(
                cv_text=source_cv[:3000],
                generated_text=generated_text[:4000],
            )
            raw = self._llm.generate(_LLM_HALLUCINATION_SYSTEM, user_prompt).strip()
            if raw.startswith("```"):
                parts = raw.split("```")
                inner = parts[1]
                if inner.startswith("json"):
                    inner = inner[4:]
                raw = inner.strip()

            items: list[dict] = json.loads(raw)
            return [
                HallucinationFlag(
                    claim=item.get("claim", ""),
                    claim_type=item.get("claim_type", "unknown"),
                    reason=item.get("reason", ""),
                    confidence=float(item.get("confidence", 0.5)),
                    context="(LLM-detected)",
                )
                for item in items
                if item.get("claim")
            ]
        except Exception as exc:
            logger.warning("LLM hallucination check failed: %s", exc)
            return []

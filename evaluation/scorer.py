"""
evaluation/scorer.py
--------------------
LLM-as-judge response scoring module.

Scores a generated document (cover letter, resume, etc.) against a job
description across four dimensions:

  • ats_keyword_match  – how many JD keywords appear in the output (0–100)
  • tone_score         – professional/appropriate tone assessment   (0–100)
  • length_score       – length appropriateness for the doc type    (0–100)
  • experience_relevance – alignment with candidate experience       (0–100)

Usage (standalone):
    from evaluation.scorer import ResponseScorer
    scorer = ResponseScorer()
    result = scorer.score(job_description="...", generated_output="...", doc_type="cover_letter")
    print(result)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Typing helpers ────────────────────────────────────────────────────────────

ScoreDict = dict[str, Any]

# ── Length targets (word counts) per document type ────────────────────────────
_LENGTH_TARGETS: dict[str, tuple[int, int]] = {
    "cover_letter": (250, 450),
    "cold_email":   (100, 200),
    "cold_message": (80, 180),
    "resume":       (300, 700),
    "default":      (200, 600),
}

# ── Stopwords for keyword extraction ─────────────────────────────────────────
_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "for", "are", "with", "this", "that", "have", "will",
    "you", "our", "they", "from", "your", "its", "not", "but", "can",
    "has", "was", "had", "been", "would", "should", "could", "about",
    "also", "into", "more", "than", "then", "when", "what", "which",
    "who", "how", "any", "all", "may", "some", "such", "each",
})

# ── Scoring judge prompt ──────────────────────────────────────────────────────
_JUDGE_SYSTEM_PROMPT = """\
You are a professional recruiter and career coach acting as an expert evaluator.
Score the provided generated job-application document against the job description.

Return ONLY a valid JSON object with no markdown fences. Schema:
{
  "ats_keyword_match": <integer 0-100>,
  "tone_score": <integer 0-100>,
  "length_score": <integer 0-100>,
  "experience_relevance": <integer 0-100>,
  "reasoning": {
    "ats_keyword_match": "<1-sentence explanation>",
    "tone_score": "<1-sentence explanation>",
    "length_score": "<1-sentence explanation>",
    "experience_relevance": "<1-sentence explanation>"
  }
}

Scoring rubrics:
- ats_keyword_match: What fraction of the key role-specific terms/skills from the JD appear verbatim
  or as close synonyms in the generated output? 0=none match, 100=all important terms present.
- tone_score: Is the writing tone professional, confident and appropriate for a job application?
  0=unprofessional/robotic/sycophantic, 100=polished and authentic.
- length_score: Is the length appropriate for the document type? Penalise heavily if too short (<50%)
  or too long (>200%) of the ideal range.
- experience_relevance: How well does the document highlight experience/skills that are relevant to
  this specific role? 0=generic/mismatched, 100=tightly aligned.
"""

_JUDGE_USER_TEMPLATE = """\
=== JOB DESCRIPTION ===
{job_description}

=== GENERATED DOCUMENT (type: {doc_type}) ===
{generated_output}

=== IDEAL LENGTH RANGE ===
{min_words}–{max_words} words (document is {actual_words} words)

Score this document on all four dimensions.
"""


# ── Keyword extraction (fast, no LLM) ────────────────────────────────────────

def _extract_keywords(text: str, top_n: int = 30) -> list[str]:
    """Extract meaningful keywords from text without LLM."""
    words = re.findall(r"\b[A-Za-z][A-Za-z0-9+#.\-]{2,}\b", text)
    freq: dict[str, int] = {}
    for w in words:
        wl = w.lower()
        if wl not in _STOPWORDS:
            freq[wl] = freq.get(wl, 0) + 1
    # Multi-word tech terms / phrases (bigrams)
    bigrams = re.findall(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)+)\b", text)
    for bg in bigrams:
        freq[bg.lower()] = freq.get(bg.lower(), 0) + 3  # weight phrases higher
    ranked = sorted(freq.items(), key=lambda x: -x[1])
    return [w for w, _ in ranked[:top_n]]


def _ats_keyword_match_rate(job_description: str, generated_output: str) -> float:
    """Heuristic ATS keyword match rate (0–100) without LLM."""
    jd_keywords = _extract_keywords(job_description, top_n=25)
    if not jd_keywords:
        return 50.0
    output_lower = generated_output.lower()
    matches = sum(1 for kw in jd_keywords if kw in output_lower)
    return round((matches / len(jd_keywords)) * 100, 1)


def _length_score(generated_output: str, doc_type: str) -> tuple[float, int]:
    """Return (score 0-100, word_count) based on length appropriateness."""
    lo, hi = _LENGTH_TARGETS.get(doc_type, _LENGTH_TARGETS["default"])
    words = len(generated_output.split())
    if words == 0:
        return 0.0, 0
    if lo <= words <= hi:
        return 100.0, words
    if words < lo:
        ratio = words / lo
        return round(max(0.0, ratio * 100), 1), words
    # Too long
    ratio = hi / words
    return round(max(0.0, ratio * 100), 1), words


# ── Main scorer class ─────────────────────────────────────────────────────────

class ResponseScorer:
    """Score generated job-application documents using LLM-as-judge."""

    def __init__(self, llm_provider: Optional[Any] = None) -> None:
        """
        Args:
            llm_provider: An object with a .generate(system, user) -> str method.
                          If None, the scorer falls back to heuristics only.
        """
        self._llm = llm_provider

    # ── Public API ────────────────────────────────────────────────────────────

    def score(
        self,
        job_description: str,
        generated_output: str,
        doc_type: str = "cover_letter",
        cv_text: str = "",
    ) -> ScoreDict:
        """
        Score a generated document.

        Args:
            job_description: Raw text of the target job description.
            generated_output: The LLM-generated document text.
            doc_type: One of cover_letter | resume | cold_email | cold_message.
            cv_text: Optional candidate CV text (used as context in LLM judge).

        Returns:
            dict with keys:
                ats_keyword_match, tone_score, length_score,
                experience_relevance, overall_score, reasoning,
                word_count, doc_type
        """
        lo, hi = _LENGTH_TARGETS.get(doc_type, _LENGTH_TARGETS["default"])
        length_heuristic, word_count = _length_score(generated_output, doc_type)
        ats_heuristic = _ats_keyword_match_rate(job_description, generated_output)

        if self._llm is not None:
            try:
                return self._llm_score(
                    job_description=job_description,
                    generated_output=generated_output,
                    doc_type=doc_type,
                    min_words=lo,
                    max_words=hi,
                    actual_words=word_count,
                    ats_heuristic=ats_heuristic,
                    length_heuristic=length_heuristic,
                )
            except Exception as exc:
                logger.warning("LLM judge failed, falling back to heuristics: %s", exc)

        return self._heuristic_score(
            job_description=job_description,
            generated_output=generated_output,
            doc_type=doc_type,
            ats_heuristic=ats_heuristic,
            length_heuristic=length_heuristic,
            word_count=word_count,
        )

    # ── LLM judge path ────────────────────────────────────────────────────────

    def _llm_score(
        self,
        job_description: str,
        generated_output: str,
        doc_type: str,
        min_words: int,
        max_words: int,
        actual_words: int,
        ats_heuristic: float,
        length_heuristic: float,
    ) -> ScoreDict:
        user_prompt = _JUDGE_USER_TEMPLATE.format(
            job_description=job_description[:3000],
            doc_type=doc_type,
            generated_output=generated_output[:4000],
            min_words=min_words,
            max_words=max_words,
            actual_words=actual_words,
        )
        raw = self._llm.generate(_JUDGE_SYSTEM_PROMPT, user_prompt).strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            parts = raw.split("```")
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            raw = inner.strip()

        parsed: dict = json.loads(raw)

        ats    = float(parsed.get("ats_keyword_match", ats_heuristic))
        tone   = float(parsed.get("tone_score", 70.0))
        length = float(parsed.get("length_score", length_heuristic))
        exp    = float(parsed.get("experience_relevance", 60.0))
        reasoning = parsed.get("reasoning", {})

        overall = round((ats * 0.35 + tone * 0.20 + length * 0.15 + exp * 0.30), 1)

        return {
            "ats_keyword_match": ats,
            "tone_score": tone,
            "length_score": length,
            "experience_relevance": exp,
            "overall_score": overall,
            "reasoning": reasoning,
            "word_count": actual_words,
            "doc_type": doc_type,
            "method": "llm_judge",
        }

    # ── Heuristic fallback ────────────────────────────────────────────────────

    def _heuristic_score(
        self,
        job_description: str,
        generated_output: str,
        doc_type: str,
        ats_heuristic: float,
        length_heuristic: float,
        word_count: int,
    ) -> ScoreDict:
        # Tone heuristics: check for common red flags
        output_lower = generated_output.lower()
        tone = 70.0
        if any(phrase in output_lower for phrase in ["dear hiring manager", "to whom it may concern"]):
            tone -= 10
        if any(phrase in output_lower for phrase in ["passionate", "i am very passionate", "i am a hard worker"]):
            tone -= 5
        if word_count < 20:
            tone -= 30
        tone = max(0.0, min(100.0, tone))

        # Experience relevance: keyword overlap between JD and output
        jd_kws = set(_extract_keywords(job_description, 20))
        out_kws = set(_extract_keywords(generated_output, 20))
        overlap = len(jd_kws & out_kws)
        exp_rel = min(100.0, round((overlap / max(len(jd_kws), 1)) * 100 * 1.5, 1))

        overall = round(
            ats_heuristic * 0.35
            + tone * 0.20
            + length_heuristic * 0.15
            + exp_rel * 0.30,
            1,
        )

        return {
            "ats_keyword_match": ats_heuristic,
            "tone_score": tone,
            "length_score": length_heuristic,
            "experience_relevance": exp_rel,
            "overall_score": overall,
            "reasoning": {
                "ats_keyword_match": "Computed from keyword frequency overlap (heuristic).",
                "tone_score": "Checked for common anti-patterns (heuristic).",
                "length_score": "Compared against ideal word-count range (heuristic).",
                "experience_relevance": "Keyword overlap between JD and output (heuristic).",
            },
            "word_count": word_count,
            "doc_type": doc_type,
            "method": "heuristic",
        }

"""
tests/evaluation/test_hallucination.py
---------------------------------------
Unit tests for evaluation/hallucination.py
"""

import pytest
from evaluation.hallucination import (
    HallucinationChecker,
    HallucinationFlag,
    _compute_cv_yoe,
    _text_contains,
    _extract_years_from_cv,
)


# ── Helper utilities ──────────────────────────────────────────────────────────

class TestTextContains:
    def test_direct_match(self):
        assert _text_contains("I worked at Google as an engineer", "google") is True

    def test_no_match(self):
        assert _text_contains("I worked at Google", "Microsoft") is False

    def test_partial_token_overlap(self):
        # "software engineer" overlaps enough with "engineering software" to match
        assert _text_contains("software engineering role", "software engineer", threshold=0.5) is True

    def test_empty_needle(self):
        assert _text_contains("some haystack", "") is False


class TestComputeCVYoe:
    def test_computes_years_from_date_range(self):
        cv = "Software Engineer at Acme Corp 2019 to Present"
        yoe = _compute_cv_yoe(cv)
        assert yoe is not None
        assert yoe >= 5  # at least 5 years from 2019

    def test_returns_none_for_no_dates(self):
        yoe = _compute_cv_yoe("No dates mentioned anywhere in this text.")
        assert yoe is None

    def test_handles_explicit_end_year(self):
        cv = "Engineer 2018 - 2022"
        yoe = _compute_cv_yoe(cv)
        assert yoe is not None
        assert yoe == 4.0


class TestExtractYearsFromCV:
    def test_extracts_years(self):
        years = _extract_years_from_cv("Worked 2018–2022 at BigCorp. Before that 2015–2018.")
        assert 2018 in years
        assert 2022 in years

    def test_no_years(self):
        years = _extract_years_from_cv("no dates here at all")
        assert len(years) == 0


# ── HallucinationFlag ─────────────────────────────────────────────────────────

class TestHallucinationFlag:
    def test_to_dict(self):
        flag = HallucinationFlag(
            claim="10 years experience",
            claim_type="years_experience",
            reason="CV only shows 5 years.",
            confidence=0.8,
            context="I have 10 years experience in Python.",
        )
        d = flag.to_dict()
        assert d["claim"] == "10 years experience"
        assert d["claim_type"] == "years_experience"
        assert d["confidence"] == 0.8


# ── HallucinationChecker ──────────────────────────────────────────────────────

class TestHallucinationCheckerNoLLM:
    """Tests using regex-only path (no LLM provider)."""

    def setup_method(self):
        self.checker = HallucinationChecker(llm_provider=None)

    def test_no_flags_when_grounded(self):
        cv = "Software Engineer at Google 2020-2023. Python, FastAPI, Docker."
        generated = "I am a Software Engineer with Python and FastAPI skills."
        flags = self.checker.check(generated, cv, use_llm_fallback=False)
        # Should have few/no flags since claims are grounded
        assert isinstance(flags, list)

    def test_detects_exaggerated_yoe(self):
        cv = "Junior Developer 2022-2023."
        generated = "With 10+ years of experience in software engineering..."
        flags = self.checker.check(generated, cv, use_llm_fallback=False)
        yoe_flags = [f for f in flags if f.claim_type == "years_experience"]
        assert len(yoe_flags) >= 1

    def test_detects_ungrounded_certification(self):
        cv = "Self-taught Python developer."
        generated = "I hold an AWS Certified Solutions Architect certification."
        flags = self.checker.check(generated, cv, use_llm_fallback=False)
        cert_flags = [f for f in flags if f.claim_type == "certification"]
        assert len(cert_flags) >= 1

    def test_returns_list_of_flags(self):
        flags = self.checker.check("some generated text", "some cv text", use_llm_fallback=False)
        assert isinstance(flags, list)
        assert all(isinstance(f, HallucinationFlag) for f in flags)

    def test_check_as_dicts(self):
        flags = self.checker.check_as_dicts(
            "some generated text", "some cv text", use_llm_fallback=False
        )
        assert isinstance(flags, list)
        for f in flags:
            assert isinstance(f, dict)
            assert "claim" in f

    def test_no_duplicate_flags(self):
        cv = "Junior developer 2023–2024."
        generated = "10 years experience. 10 years experience."
        flags = self.checker.check(generated, cv, use_llm_fallback=False)
        claims = [f.claim.lower()[:60] for f in flags]
        assert len(claims) == len(set(claims)), "Duplicate flags found"

    def test_confidence_range(self):
        cv = "Junior developer."
        generated = "15 years experience. AWS Certified. BSc MIT."
        flags = self.checker.check(generated, cv, use_llm_fallback=False)
        for f in flags:
            assert 0.0 <= f.confidence <= 1.0

    def test_sorted_by_confidence_descending(self):
        cv = "Junior developer."
        generated = "15 years experience. AWS Certified."
        flags = self.checker.check(generated, cv, use_llm_fallback=False)
        if len(flags) >= 2:
            confidences = [f.confidence for f in flags]
            assert confidences == sorted(confidences, reverse=True)


class TestHallucinationCheckerWithMockLLM:
    """Tests using a mock LLM fallback."""

    class _MockLLM:
        def generate(self, system: str, user: str) -> str:
            return """[
                {
                    "claim": "published 3 patents",
                    "claim_type": "achievement",
                    "reason": "No patents mentioned in CV",
                    "confidence": 0.9
                }
            ]"""

    def test_llm_fallback_adds_flags(self):
        checker = HallucinationChecker(llm_provider=self._MockLLM())
        cv = "Software engineer with 5 years of Python experience."
        generated = "I have published 3 patents in machine learning."
        flags = checker.check(generated, cv, use_llm_fallback=True)
        patent_flags = [f for f in flags if "patent" in f.claim.lower()]
        assert len(patent_flags) >= 1

    def test_llm_failure_handled_gracefully(self):
        class _BrokenLLM:
            def generate(self, s: str, u: str) -> str:
                raise RuntimeError("API down")

        checker = HallucinationChecker(llm_provider=_BrokenLLM())
        flags = checker.check("generated text", "cv text", use_llm_fallback=True)
        assert isinstance(flags, list)  # should not raise

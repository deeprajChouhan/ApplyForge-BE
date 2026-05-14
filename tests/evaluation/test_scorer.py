"""
tests/evaluation/test_scorer.py
--------------------------------
Unit tests for evaluation/scorer.py
"""

import pytest
from evaluation.scorer import (
    ResponseScorer,
    _ats_keyword_match_rate,
    _extract_keywords,
    _length_score,
)


# ── _extract_keywords ─────────────────────────────────────────────────────────

class TestExtractKeywords:
    def test_returns_list(self):
        kws = _extract_keywords("Python developer with FastAPI experience", top_n=10)
        assert isinstance(kws, list)

    def test_excludes_stopwords(self):
        kws = _extract_keywords("the and for are with", top_n=10)
        assert all(w not in ("the", "and", "for", "are", "with") for w in kws)

    def test_ranked_by_frequency(self):
        text = "Python Python Python Java"
        kws = _extract_keywords(text, top_n=5)
        assert kws[0] == "python"

    def test_empty_text(self):
        kws = _extract_keywords("")
        assert kws == []


# ── _ats_keyword_match_rate ───────────────────────────────────────────────────

class TestATSKeywordMatchRate:
    def test_perfect_match(self):
        # Use the same phrase in both so bigrams also match
        jd = "We need a Python FastAPI developer with SQLAlchemy skills."
        output = "I am a Python FastAPI developer with SQLAlchemy expertise."
        rate = _ats_keyword_match_rate(jd, output)
        assert rate > 60.0

    def test_no_match(self):
        jd = "Kubernetes DevOps cloud infrastructure Terraform Ansible"
        output = "I enjoy cooking pasta and hiking in the mountains every weekend."
        rate = _ats_keyword_match_rate(jd, output)
        assert rate < 20.0

    def test_empty_jd_returns_50(self):
        rate = _ats_keyword_match_rate("", "some output text")
        assert rate == 50.0

    def test_returns_float(self):
        rate = _ats_keyword_match_rate("Python developer", "Python engineer")
        assert isinstance(rate, float)


# ── _length_score ─────────────────────────────────────────────────────────────

class TestLengthScore:
    def test_perfect_length_cover_letter(self):
        # 350 words — in range (250–450)
        text = "word " * 350
        score, wc = _length_score(text, "cover_letter")
        assert score == 100.0
        assert wc == 350

    def test_too_short_cover_letter(self):
        text = "word " * 50  # below 250
        score, wc = _length_score(text, "cover_letter")
        assert score < 100.0
        assert score > 0.0

    def test_too_long_cover_letter(self):
        text = "word " * 900  # above 450
        score, _ = _length_score(text, "cover_letter")
        assert score < 100.0

    def test_empty_text_returns_zero(self):
        score, wc = _length_score("", "cover_letter")
        assert score == 0.0
        assert wc == 0

    def test_default_doc_type(self):
        text = "word " * 400
        score, _ = _length_score(text, "unknown_type")
        assert isinstance(score, float)


# ── ResponseScorer ────────────────────────────────────────────────────────────

class TestResponseScorerHeuristic:
    """Tests using the heuristic path (no LLM provider)."""

    def setup_method(self):
        self.scorer = ResponseScorer(llm_provider=None)

    def test_score_returns_dict(self):
        result = self.scorer.score(
            job_description="Python FastAPI developer",
            generated_output="I am a Python FastAPI developer with 3 years of experience.",
            doc_type="cover_letter",
        )
        assert isinstance(result, dict)

    def test_score_has_required_keys(self):
        result = self.scorer.score(
            job_description="Python developer",
            generated_output="Python engineer with FastAPI.",
            doc_type="cold_email",
        )
        required = {
            "ats_keyword_match", "tone_score", "length_score",
            "experience_relevance", "overall_score", "reasoning",
            "word_count", "doc_type", "method",
        }
        assert required.issubset(result.keys())

    def test_score_values_in_range(self):
        result = self.scorer.score(
            job_description="Python backend engineer",
            generated_output="Experienced Python backend developer. " * 50,
            doc_type="cover_letter",
        )
        for key in ("ats_keyword_match", "tone_score", "length_score",
                    "experience_relevance", "overall_score"):
            assert 0.0 <= result[key] <= 100.0, f"{key} out of range"

    def test_heuristic_method_label(self):
        result = self.scorer.score("jd text", "output text", "cover_letter")
        assert result["method"] == "heuristic"

    def test_doc_type_preserved(self):
        result = self.scorer.score("jd", "output", "resume")
        assert result["doc_type"] == "resume"

    def test_word_count_correct(self):
        output = "one two three four five"
        result = self.scorer.score("jd text", output, "cover_letter")
        assert result["word_count"] == 5


class TestResponseScorerWithMockLLM:
    """Tests using a mock LLM provider."""

    class _MockLLM:
        def generate(self, system: str, user: str) -> str:
            return """{
                "ats_keyword_match": 85,
                "tone_score": 90,
                "length_score": 75,
                "experience_relevance": 80,
                "reasoning": {
                    "ats_keyword_match": "Good keyword coverage.",
                    "tone_score": "Professional tone.",
                    "length_score": "Slightly short.",
                    "experience_relevance": "Well aligned."
                }
            }"""

    def test_llm_scores_used(self):
        scorer = ResponseScorer(llm_provider=self._MockLLM())
        result = scorer.score("Python developer JD", "I am a Python dev.", "cover_letter")
        assert result["ats_keyword_match"] == 85.0
        assert result["tone_score"] == 90.0
        assert result["method"] == "llm_judge"

    def test_overall_score_is_weighted_average(self):
        scorer = ResponseScorer(llm_provider=self._MockLLM())
        result = scorer.score("jd", "output", "cover_letter")
        expected = round(85 * 0.35 + 90 * 0.20 + 75 * 0.15 + 80 * 0.30, 1)
        assert result["overall_score"] == expected

    def test_llm_failure_falls_back_to_heuristic(self):
        class _BrokenLLM:
            def generate(self, s: str, u: str) -> str:
                raise RuntimeError("API unavailable")

        scorer = ResponseScorer(llm_provider=_BrokenLLM())
        result = scorer.score("jd", "output text here", "cover_letter")
        assert result["method"] == "heuristic"

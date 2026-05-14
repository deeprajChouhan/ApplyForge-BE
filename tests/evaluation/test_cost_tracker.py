"""
tests/evaluation/test_cost_tracker.py
--------------------------------------
Unit tests for evaluation/cost_tracker.py
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from evaluation.cost_tracker import (
    CostTracker,
    estimate_cost,
    estimate_tokens,
    track_llm_call,
)


# ── estimate_tokens ───────────────────────────────────────────────────────────

class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") >= 1  # minimum 1

    def test_returns_int(self):
        assert isinstance(estimate_tokens("hello world"), int)

    def test_longer_text_more_tokens(self):
        short = estimate_tokens("hello")
        long  = estimate_tokens("hello " * 100)
        assert long > short


# ── estimate_cost ─────────────────────────────────────────────────────────────

class TestEstimateCost:
    def test_known_model_returns_float(self):
        cost = estimate_cost("gpt-4o-mini", 1000, 500)
        assert isinstance(cost, float)
        assert cost > 0.0

    def test_gpt4o_mini_pricing(self):
        # 1000 prompt @ $0.00015/1k + 1000 completion @ $0.0006/1k = $0.00075
        cost = estimate_cost("gpt-4o-mini", 1000, 1000)
        assert abs(cost - 0.00075) < 1e-7

    def test_unknown_model_uses_default(self):
        cost = estimate_cost("unknown-model-xyz", 1000, 1000)
        assert cost > 0.0  # falls back to default pricing

    def test_zero_tokens(self):
        cost = estimate_cost("gpt-4o-mini", 0, 0)
        assert cost == 0.0

    def test_partial_model_name_match(self):
        # "gpt-4o-mini-2024-07-18" should match "gpt-4o-mini"
        cost = estimate_cost("gpt-4o-mini-2024-07-18", 1000, 500)
        assert cost > 0.0


# ── track_llm_call decorator ──────────────────────────────────────────────────

class TestTrackLLMCallDecorator:
    def test_decorator_returns_result(self):
        @track_llm_call(model="gpt-4o-mini", operation="test")
        def my_func(a: str, b: str) -> str:
            return "result"

        # Patch _write_log to avoid DB calls in tests
        with patch("evaluation.cost_tracker._write_log") as mock_log:
            result = my_func("system", "user")
        assert result == "result"

    def test_decorator_calls_write_log(self):
        @track_llm_call(model="gpt-4o-mini", operation="test_op")
        def my_func() -> str:
            return "response text here"

        with patch("evaluation.cost_tracker._write_log") as mock_log:
            my_func()
            mock_log.assert_called_once()
            kwargs = mock_log.call_args.kwargs
            assert kwargs["model_name"] == "gpt-4o-mini"
            assert kwargs["operation"] == "test_op"
            assert kwargs["latency_ms"] >= 0.0
            assert kwargs["total_tokens"] > 0

    def test_decorator_measures_latency(self):
        @track_llm_call(model="gpt-4o-mini")
        def slow_func() -> str:
            time.sleep(0.05)
            return "done"

        with patch("evaluation.cost_tracker._write_log") as mock_log:
            slow_func()
            kwargs = mock_log.call_args.kwargs
            assert kwargs["latency_ms"] >= 40.0  # at least 40ms

    def test_decorator_preserves_exceptions(self):
        @track_llm_call(model="gpt-4o-mini")
        def failing_func() -> str:
            raise ValueError("test error")

        with patch("evaluation.cost_tracker._write_log"):
            with pytest.raises(ValueError, match="test error"):
                failing_func()


# ── CostTracker ───────────────────────────────────────────────────────────────

class TestCostTracker:
    def _make_mock_provider(self, response: str = "mock response text") -> MagicMock:
        provider = MagicMock()
        provider.generate.return_value = response
        return provider

    def test_generate_delegates_to_provider(self):
        provider = self._make_mock_provider("cover letter output")
        tracker  = CostTracker(provider=provider, model="gpt-4o-mini")

        with patch("evaluation.cost_tracker._write_log"):
            result = tracker.generate("system", "user")

        assert result == "cover letter output"
        provider.generate.assert_called_once_with("system", "user")

    def test_generate_logs_usage(self):
        provider = self._make_mock_provider()
        tracker  = CostTracker(
            provider=provider,
            model="gpt-4o-mini",
            application_id=99,
            user_id=7,
        )

        with patch("evaluation.cost_tracker._write_log") as mock_log:
            tracker.generate("system prompt", "user prompt")
            mock_log.assert_called_once()
            kwargs = mock_log.call_args.kwargs
            assert kwargs["application_id"] == 99
            assert kwargs["user_id"] == 7
            assert kwargs["model_name"] == "gpt-4o-mini"
            assert kwargs["estimated_cost_usd"] >= 0.0

    def test_generate_operation_override(self):
        provider = self._make_mock_provider()
        tracker  = CostTracker(provider=provider, model="gpt-4o-mini", operation="default_op")

        with patch("evaluation.cost_tracker._write_log") as mock_log:
            tracker.generate("s", "u", operation="override_op")
            kwargs = mock_log.call_args.kwargs
            assert kwargs["operation"] == "override_op"

    def test_latency_recorded(self):
        provider = self._make_mock_provider()
        tracker  = CostTracker(provider=provider, model="gpt-4o-mini")

        with patch("evaluation.cost_tracker._write_log") as mock_log:
            tracker.generate("s", "u")
            kwargs = mock_log.call_args.kwargs
            assert kwargs["latency_ms"] >= 0.0

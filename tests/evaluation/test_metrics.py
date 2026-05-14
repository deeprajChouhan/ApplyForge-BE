"""
tests/evaluation/test_metrics.py
----------------------------------
Unit tests for evaluation/metrics.py

Uses MagicMock to simulate DB query results — no real DB needed.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from evaluation.metrics import MetricsAggregator


def _make_mock_db() -> MagicMock:
    """Return a SQLAlchemy Session mock."""
    return MagicMock()


class TestHallucinationRate:
    def test_no_data_returns_zero(self):
        db = _make_mock_db()
        # Simulate empty query results
        mock_q = MagicMock()
        mock_q.count.return_value = 0
        mock_q.filter.return_value = mock_q
        db.query.return_value = mock_q

        agg = MetricsAggregator(db)
        with patch.object(agg, "hallucination_rate", return_value={"rate_pct": 0.0, "flagged": 0, "total_evals": 0}):
            result = agg.hallucination_rate()
        assert result["rate_pct"] == 0.0

    def test_all_flagged_returns_100(self):
        db = _make_mock_db()
        mock_q = MagicMock()
        mock_q.count.side_effect = [10, 10]  # total=10, flagged=10
        mock_q.filter.return_value = mock_q
        db.query.return_value = mock_q

        agg = MetricsAggregator(db)
        with patch.object(agg, "hallucination_rate", return_value={"rate_pct": 100.0, "flagged": 10, "total_evals": 10}):
            result = agg.hallucination_rate()
        assert result["rate_pct"] == 100.0


class TestCostPerApplication:
    def test_no_data_returns_zeros(self):
        agg = MetricsAggregator(_make_mock_db())
        with patch.object(agg, "cost_per_application", return_value={
            "avg_cost_usd": 0.0,
            "total_cost_usd": 0.0,
            "applications_tracked": 0,
        }):
            result = agg.cost_per_application()
        assert result["avg_cost_usd"] == 0.0
        assert result["applications_tracked"] == 0

    def test_returns_correct_average(self):
        agg = MetricsAggregator(_make_mock_db())
        with patch.object(agg, "cost_per_application", return_value={
            "avg_cost_usd": 0.015,
            "total_cost_usd": 0.045,
            "applications_tracked": 3,
        }):
            result = agg.cost_per_application()
        assert result["avg_cost_usd"] == 0.015
        assert result["total_cost_usd"] == 0.045


class TestLatencyPercentiles:
    def test_no_data_returns_zeros(self):
        agg = MetricsAggregator(_make_mock_db())
        with patch.object(agg, "latency_percentiles", return_value={
            "latency_p50_ms": 0.0,
            "latency_p95_ms": 0.0,
        }):
            result = agg.latency_percentiles()
        assert result["latency_p50_ms"] == 0.0

    def test_p95_greater_than_or_equal_p50(self):
        agg = MetricsAggregator(_make_mock_db())
        with patch.object(agg, "latency_percentiles", return_value={
            "latency_p50_ms": 800.0,
            "latency_p95_ms": 2100.0,
        }):
            result = agg.latency_percentiles()
        assert result["latency_p95_ms"] >= result["latency_p50_ms"]


class TestOutcomeConversionRate:
    def test_no_data(self):
        agg = MetricsAggregator(_make_mock_db())
        with patch.object(agg, "outcome_conversion_rate", return_value={
            "by_outcome": {},
            "positive_rate_pct": 0.0,
            "total_with_outcome": 0,
        }):
            result = agg.outcome_conversion_rate()
        assert result["positive_rate_pct"] == 0.0

    def test_all_offers_is_100_pct(self):
        agg = MetricsAggregator(_make_mock_db())
        with patch.object(agg, "outcome_conversion_rate", return_value={
            "by_outcome": {"offer": 5},
            "positive_rate_pct": 100.0,
            "total_with_outcome": 5,
        }):
            result = agg.outcome_conversion_rate()
        assert result["positive_rate_pct"] == 100.0

    def test_mixed_outcomes(self):
        agg = MetricsAggregator(_make_mock_db())
        with patch.object(agg, "outcome_conversion_rate", return_value={
            "by_outcome": {"interview": 3, "rejected": 7},
            "positive_rate_pct": 30.0,
            "total_with_outcome": 10,
        }):
            result = agg.outcome_conversion_rate()
        assert result["positive_rate_pct"] == 30.0


class TestDashboard:
    def test_dashboard_has_all_keys(self):
        agg = MetricsAggregator(_make_mock_db())

        expected_dashboard = {
            "generated_at": datetime.utcnow().isoformat(),
            "since": datetime.utcnow().isoformat(),
            "avg_scores_by_job_type": [],
            "hallucination_rate": {"rate_pct": 0.0, "flagged": 0, "total_evals": 0},
            "cost_per_application": {"avg_cost_usd": 0.0, "total_cost_usd": 0.0, "applications_tracked": 0},
            "latency_p50_ms": 0.0,
            "latency_p95_ms": 0.0,
            "outcome_conversion_rate": {"by_outcome": {}, "positive_rate_pct": 0.0, "total_with_outcome": 0},
            "total_evaluations": 0,
            "total_llm_calls": 0,
        }

        with patch.object(agg, "dashboard", return_value=expected_dashboard):
            result = agg.dashboard()

        required_keys = {
            "generated_at", "since", "avg_scores_by_job_type",
            "hallucination_rate", "cost_per_application",
            "latency_p50_ms", "latency_p95_ms",
            "outcome_conversion_rate", "total_evaluations", "total_llm_calls",
        }
        assert required_keys.issubset(result.keys())

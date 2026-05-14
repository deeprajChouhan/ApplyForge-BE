"""
evaluation/metrics.py
---------------------
Metrics aggregation module.

Queries the database and computes:
  • average_scores_by_job_type   – avg overall_score grouped by role_title
  • hallucination_rate           – % of evals with ≥1 hallucination flag
  • cost_per_application         – avg total LLM cost per application
  • latency_percentiles          – p50 / p95 latency across all LLM calls
  • outcome_conversion_rate      – % of tracked outcomes that are interview/offer

All methods return plain Python dicts/lists for easy JSON serialisation.

Usage (standalone):
    from evaluation.metrics import MetricsAggregator
    from app.db.session import SessionLocal
    agg = MetricsAggregator(db=SessionLocal())
    print(agg.dashboard())
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class MetricsAggregator:
    """Compute evaluation metrics from the database."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Public: Dashboard summary ─────────────────────────────────────────────

    def dashboard(
        self,
        since_days: int = 30,
        user_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Return all metrics in a single dict for the dashboard endpoint.

        Args:
            since_days: Look-back window in days.
            user_id:    Optional filter for a specific user.

        Returns:
            dict with keys:
                generated_at, since, avg_scores_by_job_type,
                hallucination_rate, cost_per_application,
                latency_p50_ms, latency_p95_ms,
                outcome_conversion_rate, total_evaluations, total_llm_calls
        """
        since = datetime.utcnow() - timedelta(days=since_days)

        return {
            "generated_at":             datetime.utcnow().isoformat(),
            "since":                    since.isoformat(),
            "avg_scores_by_job_type":   self.avg_scores_by_job_type(since, user_id),
            "hallucination_rate":       self.hallucination_rate(since, user_id),
            "cost_per_application":     self.cost_per_application(since, user_id),
            **self.latency_percentiles(since, user_id),
            "outcome_conversion_rate":  self.outcome_conversion_rate(since, user_id),
            "total_evaluations":        self._total_evaluations(since, user_id),
            "total_llm_calls":          self._total_llm_calls(since, user_id),
        }

    # ── avg_scores_by_job_type ────────────────────────────────────────────────

    def avg_scores_by_job_type(
        self,
        since: Optional[datetime] = None,
        user_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """
        Returns list of dicts with job type (role_title) and avg scores.
        """
        from app.models.evaluation_models import ApplicationEvaluation
        from app.models.models import JobApplication

        q = (
            self.db.query(
                JobApplication.role_title,
                func.avg(ApplicationEvaluation.overall_score).label("avg_overall"),
                func.avg(ApplicationEvaluation.ats_keyword_match).label("avg_ats"),
                func.avg(ApplicationEvaluation.experience_relevance).label("avg_exp_rel"),
                func.count(ApplicationEvaluation.id).label("count"),
            )
            .join(JobApplication, ApplicationEvaluation.application_id == JobApplication.id)
            .filter(ApplicationEvaluation.overall_score.isnot(None))
        )
        if since:
            q = q.filter(ApplicationEvaluation.evaluated_at >= since)
        if user_id is not None:
            q = q.filter(ApplicationEvaluation.user_id == user_id)

        q = q.group_by(JobApplication.role_title).order_by(func.avg(ApplicationEvaluation.overall_score).desc())

        rows = q.all()
        return [
            {
                "role_title":   r.role_title,
                "avg_overall":  round(r.avg_overall or 0, 1),
                "avg_ats":      round(r.avg_ats     or 0, 1),
                "avg_exp_rel":  round(r.avg_exp_rel or 0, 1),
                "count":        r.count,
            }
            for r in rows
        ]

    # ── hallucination_rate ────────────────────────────────────────────────────

    def hallucination_rate(
        self,
        since: Optional[datetime] = None,
        user_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Returns overall hallucination rate as a percentage and raw counts.
        """
        from app.models.evaluation_models import ApplicationEvaluation

        q = self.db.query(ApplicationEvaluation)
        if since:
            q = q.filter(ApplicationEvaluation.evaluated_at >= since)
        if user_id is not None:
            q = q.filter(ApplicationEvaluation.user_id == user_id)
        q = q.filter(ApplicationEvaluation.hallucination_count.isnot(None))

        total = q.count()
        flagged = q.filter(ApplicationEvaluation.hallucination_count > 0).count()
        rate = round((flagged / total * 100) if total > 0 else 0.0, 1)

        return {
            "rate_pct":    rate,
            "flagged":     flagged,
            "total_evals": total,
        }

    # ── cost_per_application ──────────────────────────────────────────────────

    def cost_per_application(
        self,
        since: Optional[datetime] = None,
        user_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Returns average LLM cost (USD) per unique application_id.
        """
        from app.models.evaluation_models import LLMUsageLog

        q = (
            self.db.query(
                LLMUsageLog.application_id,
                func.sum(LLMUsageLog.estimated_cost_usd).label("total_cost"),
            )
            .filter(LLMUsageLog.application_id.isnot(None))
        )
        if since:
            q = q.filter(LLMUsageLog.called_at >= since)
        if user_id is not None:
            q = q.filter(LLMUsageLog.user_id == user_id)

        q = q.group_by(LLMUsageLog.application_id)
        rows = q.all()

        if not rows:
            return {"avg_cost_usd": 0.0, "total_cost_usd": 0.0, "applications_tracked": 0}

        costs = [r.total_cost or 0.0 for r in rows]
        return {
            "avg_cost_usd":          round(statistics.mean(costs), 6),
            "total_cost_usd":        round(sum(costs), 6),
            "applications_tracked":  len(rows),
        }

    # ── latency_percentiles ───────────────────────────────────────────────────

    def latency_percentiles(
        self,
        since: Optional[datetime] = None,
        user_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Returns p50 and p95 latency (ms) across all LLM calls.
        """
        from app.models.evaluation_models import LLMUsageLog

        q = self.db.query(LLMUsageLog.latency_ms)
        if since:
            q = q.filter(LLMUsageLog.called_at >= since)
        if user_id is not None:
            q = q.filter(LLMUsageLog.user_id == user_id)

        latencies = [r[0] for r in q.all() if r[0] is not None]
        if not latencies:
            return {"latency_p50_ms": 0.0, "latency_p95_ms": 0.0}

        latencies.sort()
        p50 = statistics.median(latencies)
        p95_idx = max(0, int(len(latencies) * 0.95) - 1)
        p95 = latencies[p95_idx]

        return {
            "latency_p50_ms": round(p50, 1),
            "latency_p95_ms": round(p95, 1),
        }

    # ── outcome_conversion_rate ───────────────────────────────────────────────

    def outcome_conversion_rate(
        self,
        since: Optional[datetime] = None,
        user_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Returns conversion rates by outcome type and overall positive rate
        (interview + offer) among recorded outcomes.
        """
        from app.models.evaluation_models import ApplicationEvaluation

        q = (
            self.db.query(
                ApplicationEvaluation.outcome,
                func.count(ApplicationEvaluation.id).label("count"),
            )
            .filter(ApplicationEvaluation.outcome.isnot(None))
        )
        if since:
            q = q.filter(ApplicationEvaluation.evaluated_at >= since)
        if user_id is not None:
            q = q.filter(ApplicationEvaluation.user_id == user_id)

        q = q.group_by(ApplicationEvaluation.outcome)
        rows = q.all()

        if not rows:
            return {"by_outcome": {}, "positive_rate_pct": 0.0, "total_with_outcome": 0}

        by_outcome: dict[str, int] = {r.outcome: r.count for r in rows}
        total = sum(by_outcome.values())
        positive = by_outcome.get("interview", 0) + by_outcome.get("offer", 0)
        positive_rate = round((positive / total * 100) if total > 0 else 0.0, 1)

        return {
            "by_outcome":        by_outcome,
            "positive_rate_pct": positive_rate,
            "total_with_outcome": total,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _total_evaluations(
        self,
        since: Optional[datetime],
        user_id: Optional[int],
    ) -> int:
        from app.models.evaluation_models import ApplicationEvaluation
        q = self.db.query(func.count(ApplicationEvaluation.id))
        if since:
            q = q.filter(ApplicationEvaluation.evaluated_at >= since)
        if user_id is not None:
            q = q.filter(ApplicationEvaluation.user_id == user_id)
        return q.scalar() or 0

    def _total_llm_calls(
        self,
        since: Optional[datetime],
        user_id: Optional[int],
    ) -> int:
        from app.models.evaluation_models import LLMUsageLog
        q = self.db.query(func.count(LLMUsageLog.id))
        if since:
            q = q.filter(LLMUsageLog.called_at >= since)
        if user_id is not None:
            q = q.filter(LLMUsageLog.user_id == user_id)
        return q.scalar() or 0

"""
evaluation/cost_tracker.py -- LLM cost and latency tracking.

Wraps LLM provider calls to capture model name, token counts,
estimated cost (OpenAI pricing), latency ms, and timestamp.
Logs are written to the Postgres llm_usage_logs table.
"""
from __future__ import annotations

import functools
import logging
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Generator, Optional, TypeVar

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])

# Pricing lives in app.core.llm_pricing (shared with the admin cost views).
from app.core.llm_pricing import OPENAI_PRICING, _DEFAULT_PRICING, estimate_cost  # noqa: E402,F401


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~0.75 tokens/word). Fallback when provider omits usage."""
    return max(1, int(len(text.split()) * 0.75 + len(text) / 4))


def _write_log(
    *,
    model_name: str,
    operation: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    estimated_cost_usd: float,
    latency_ms: float,
    called_at: datetime,
    application_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> None:
    """Persist a usage log row to llm_usage_logs. Silently swallows DB errors."""
    try:
        from app.db.session import SessionLocal
        from app.models.evaluation_models import LLMUsageLog
        db = SessionLocal()
        try:
            db.add(LLMUsageLog(
                application_id=application_id,
                user_id=user_id,
                model_name=model_name,
                operation=operation,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=estimated_cost_usd,
                latency_ms=latency_ms,
                called_at=called_at,
            ))
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("cost_tracker: DB write failed: %s", exc)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("cost_tracker: could not import DB session: %s", exc)


def track_llm_call(
    model: str = "unknown",
    operation: str = "generate",
    application_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> Callable[[F], F]:
    """
    Decorator that logs cost/latency for any LLM-calling function.

        @track_llm_call(model="gpt-4o-mini", operation="score")
        def run_llm(system: str, user: str) -> str: ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            _model = kwargs.pop("_track_model", model)
            _op = kwargs.pop("_track_operation", operation)
            _app_id = kwargs.pop("_track_application_id", application_id)
            _uid = kwargs.pop("_track_user_id", user_id)

            t_start = time.perf_counter()
            called_at = datetime.utcnow()
            result = func(*args, **kwargs)
            latency_ms = (time.perf_counter() - t_start) * 1000

            if isinstance(result, dict) and "_usage" in result:
                usage = result.pop("_usage", {})
                pt = usage.get("prompt_tokens", 0)
                ct = usage.get("completion_tokens", 0)
            else:
                text_in = " ".join(str(a) for a in args) + " ".join(str(v) for v in kwargs.values())
                pt = estimate_tokens(text_in)
                ct = estimate_tokens(str(result) if result else "")

            cost = estimate_cost(_model, pt, ct)
            _write_log(
                model_name=_model, operation=_op,
                prompt_tokens=pt, completion_tokens=ct,
                total_tokens=pt + ct, estimated_cost_usd=cost,
                latency_ms=latency_ms, called_at=called_at,
                application_id=_app_id, user_id=_uid,
            )
            return result
        return wrapper  # type: ignore[return-value]
    return decorator


@contextmanager
def track_llm_context(
    model: str,
    operation: str = "generate",
    prompt_text: str = "",
    application_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> Generator["_UsageContext", None, None]:
    """
    Context-manager variant.

        with track_llm_context("gpt-4o-mini", "score", prompt) as ctx:
            result = my_llm_call(prompt)
            ctx.completion_text = result
    """
    ctx = _UsageContext(prompt_text=prompt_text)
    t_start = time.perf_counter()
    called_at = datetime.utcnow()
    try:
        yield ctx
    finally:
        latency_ms = (time.perf_counter() - t_start) * 1000
        pt = ctx.prompt_tokens or estimate_tokens(prompt_text)
        ct = ctx.completion_tokens or estimate_tokens(ctx.completion_text)
        _write_log(
            model_name=model, operation=operation,
            prompt_tokens=pt, completion_tokens=ct,
            total_tokens=pt + ct,
            estimated_cost_usd=estimate_cost(model, pt, ct),
            latency_ms=latency_ms, called_at=called_at,
            application_id=application_id, user_id=user_id,
        )


class _UsageContext:
    def __init__(self, prompt_text: str = "") -> None:
        self.prompt_text: str = prompt_text
        self.completion_text: str = ""
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0


class CostTracker:
    """
    Wraps an LLMProvider so every .generate() call is automatically tracked.

        tracker = CostTracker(provider=get_llm_provider(), model="gpt-4o-mini", application_id=42)
        result  = tracker.generate(system_prompt, user_prompt)
    """

    def __init__(
        self,
        provider: Any,
        model: str = "gpt-4o-mini",
        application_id: Optional[int] = None,
        user_id: Optional[int] = None,
        operation: str = "generate",
    ) -> None:
        self._provider = provider
        self._model = model
        self._application_id = application_id
        self._user_id = user_id
        self._operation = operation

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        operation: Optional[str] = None,
        application_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> str:
        _op = operation or self._operation
        _app_id = application_id if application_id is not None else self._application_id
        _uid = user_id if user_id is not None else self._user_id

        prompt_text = f"{system_prompt}\n{user_prompt}"
        t_start = time.perf_counter()
        called_at = datetime.utcnow()
        result = self._provider.generate(system_prompt, user_prompt)
        latency_ms = (time.perf_counter() - t_start) * 1000

        pt = estimate_tokens(prompt_text)
        ct = estimate_tokens(result)
        _write_log(
            model_name=self._model,
            operation=_op,
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=pt + ct,
            estimated_cost_usd=estimate_cost(self._model, pt, ct),
            latency_ms=latency_ms,
            called_at=called_at,
            application_id=_app_id,
            user_id=_uid,
        )
        return result

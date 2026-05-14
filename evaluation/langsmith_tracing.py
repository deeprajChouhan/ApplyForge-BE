"""
evaluation/langsmith_tracing.py
--------------------------------
LangSmith tracing integration using the langsmith SDK (no LangChain required).

All LLM calls are wrapped as named runs in LangSmith with metadata tags for
job_id and application_id.

When LANGSMITH_API_KEY is not set or LANGSMITH_ENABLED=false, tracing is a no-op
so the app works normally without LangSmith credentials.

Usage:
    from evaluation.langsmith_tracing import traced_llm_call, TracedLLMProvider

    # 1. Decorate a function:
    @traced_llm_call(name="generate_cover_letter", tags=["cover_letter"])
    def my_llm_call(system: str, user: str) -> str: ...

    # 2. Wrap an LLMProvider:
    provider = TracedLLMProvider(
        provider=get_llm_provider(),
        run_name="cover_letter_generation",
        application_id=42,
        job_id="some-job-ref",
    )
    result = provider.generate(system_prompt, user_prompt)
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Any, Callable, Optional, TypeVar

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# ── LangSmith availability check ──────────────────────────────────────────────

def _langsmith_enabled() -> bool:
    """Check whether LangSmith tracing is configured and enabled."""
    enabled_flag = os.getenv("LANGSMITH_ENABLED", "true").lower()
    if enabled_flag in ("false", "0", "no"):
        return False
    api_key = os.getenv("LANGSMITH_API_KEY", "")
    return bool(api_key)


def _get_client() -> Optional[Any]:
    """
    Return a langsmith.Client instance or None if unavailable.

    We do the import lazily so the module can be imported even without
    the langsmith package installed.
    """
    if not _langsmith_enabled():
        return None
    try:
        from langsmith import Client  # type: ignore[import-untyped]
        client = Client()
        return client
    except ImportError:
        logger.warning(
            "langsmith package not installed — tracing disabled. "
            "Run: pip install langsmith"
        )
        return None
    except Exception as exc:
        logger.warning("LangSmith client init failed: %s — tracing disabled.", exc)
        return None


# ── Low-level run helpers ─────────────────────────────────────────────────────

def _create_run(
    client: Any,
    *,
    name: str,
    inputs: dict[str, Any],
    tags: list[str],
    metadata: dict[str, Any],
    run_type: str = "llm",
) -> Optional[str]:
    """Start a LangSmith run and return its run_id (or None on failure)."""
    try:
        import uuid
        run_id = str(uuid.uuid4())
        client.create_run(
            id=run_id,
            name=name,
            run_type=run_type,
            inputs=inputs,
            tags=tags,
            extra={"metadata": metadata},
        )
        return run_id
    except Exception as exc:
        logger.debug("LangSmith create_run failed: %s", exc)
        return None


def _end_run(
    client: Any,
    run_id: str,
    *,
    outputs: dict[str, Any],
    error: Optional[str] = None,
) -> None:
    """Finish a LangSmith run with outputs or an error."""
    try:
        if error:
            client.update_run(run_id, error=error, end_time=None)
        else:
            client.update_run(run_id, outputs=outputs)
    except Exception as exc:
        logger.debug("LangSmith end_run failed: %s", exc)


# ── Decorator ─────────────────────────────────────────────────────────────────

def traced_llm_call(
    name: str,
    tags: Optional[list[str]] = None,
    application_id: Optional[int] = None,
    job_id: Optional[str] = None,
    project_name: Optional[str] = None,
) -> Callable[[F], F]:
    """
    Decorator that wraps a function as a named LangSmith run.

    The wrapped function must accept (system_prompt: str, user_prompt: str)
    and return a string.

    Args:
        name:           LangSmith run name (shown in the UI).
        tags:           Optional list of string tags for filtering.
        application_id: ApplyForge application ID — stored as metadata.
        job_id:         Job reference string — stored as metadata.
        project_name:   LangSmith project override (defaults to env var).
    """
    _tags = tags or []
    _metadata: dict[str, Any] = {}
    if application_id is not None:
        _metadata["application_id"] = application_id
    if job_id is not None:
        _metadata["job_id"] = job_id

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Allow per-call overrides via keyword args
            _app_id  = kwargs.pop("_ls_application_id", application_id)
            _j_id    = kwargs.pop("_ls_job_id", job_id)
            _run_name = kwargs.pop("_ls_run_name", name)

            meta = dict(_metadata)
            if _app_id is not None:
                meta["application_id"] = _app_id
            if _j_id is not None:
                meta["job_id"] = _j_id

            client = _get_client()
            run_id: Optional[str] = None

            # Capture inputs
            inputs: dict[str, Any] = {}
            if args:
                inputs["system_prompt"] = str(args[0])[:500] if len(args) > 0 else ""
                inputs["user_prompt"]   = str(args[1])[:500] if len(args) > 1 else ""
            inputs.update({k: str(v)[:200] for k, v in kwargs.items()})

            if client is not None:
                run_id = _create_run(
                    client,
                    name=_run_name,
                    inputs=inputs,
                    tags=_tags + [f"app:{_app_id}" if _app_id else "", f"job:{_j_id}" if _j_id else ""],
                    metadata=meta,
                )

            error_msg: Optional[str] = None
            result: Any = None
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as exc:
                error_msg = str(exc)
                raise
            finally:
                if client is not None and run_id is not None:
                    outputs = {"output": str(result)[:500] if result is not None else ""}
                    _end_run(client, run_id, outputs=outputs, error=error_msg)

        return wrapper  # type: ignore[return-value]
    return decorator


# ── TracedLLMProvider ─────────────────────────────────────────────────────────

class TracedLLMProvider:
    """
    Wraps any LLMProvider so every .generate() call is traced in LangSmith.

    Compatible with the existing OpenAILLMProvider / MockLLMProvider interface.

    Example:
        from app.services.ai.factory import get_llm_provider
        from evaluation.langsmith_tracing import TracedLLMProvider

        provider = TracedLLMProvider(
            provider=get_llm_provider(),
            run_name="cover_letter_generation",
            application_id=42,
        )
        text = provider.generate(system_prompt, user_prompt)
    """

    def __init__(
        self,
        provider: Any,
        run_name: str = "llm_generate",
        tags: Optional[list[str]] = None,
        application_id: Optional[int] = None,
        job_id: Optional[str] = None,
    ) -> None:
        self._provider       = provider
        self._run_name       = run_name
        self._tags           = tags or ["applyforge"]
        self._application_id = application_id
        self._job_id         = job_id
        self._client         = _get_client()

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        application_id: Optional[int] = None,
        job_id: Optional[str] = None,
    ) -> str:
        """Generate text with LangSmith tracing."""
        _app_id  = application_id if application_id is not None else self._application_id
        _job_id  = job_id         if job_id         is not None else self._job_id

        meta: dict[str, Any] = {}
        if _app_id is not None:
            meta["application_id"] = _app_id
        if _job_id is not None:
            meta["job_id"] = _job_id

        tags = list(self._tags)
        if _app_id:
            tags.append(f"app:{_app_id}")

        run_id: Optional[str] = None
        if self._client is not None:
            run_id = _create_run(
                self._client,
                name=self._run_name,
                inputs={
                    "system_prompt": system_prompt[:500],
                    "user_prompt":   user_prompt[:500],
                },
                tags=tags,
                metadata=meta,
            )

        error_msg: Optional[str] = None
        result = ""
        try:
            result = self._provider.generate(system_prompt, user_prompt)
            return result
        except Exception as exc:
            error_msg = str(exc)
            raise
        finally:
            if self._client is not None and run_id is not None:
                _end_run(
                    self._client,
                    run_id,
                    outputs={"output": result[:500]},
                    error=error_msg,
                )

    # Proxy any other attributes to the underlying provider
    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


# ── Factory helper ────────────────────────────────────────────────────────────

def get_traced_provider(
    run_name: str = "llm_generate",
    application_id: Optional[int] = None,
    job_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> Any:
    """
    Return a TracedLLMProvider wrapping the configured LLM provider.

    Falls back to the raw provider if LangSmith is not enabled.
    """
    from app.services.ai.factory import get_llm_provider

    provider = get_llm_provider()
    if not _langsmith_enabled():
        return provider
    return TracedLLMProvider(
        provider=provider,
        run_name=run_name,
        tags=tags or ["applyforge"],
        application_id=application_id,
        job_id=job_id,
    )

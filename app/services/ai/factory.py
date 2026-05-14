from __future__ import annotations

from functools import lru_cache

import structlog

from app.core.config import settings
from app.services.ai.exceptions import AIProviderConfigError
from app.services.ai.providers import (
    EmbeddingProvider,
    LLMProvider,
    MockEmbeddingProvider,
    MockLLMProvider,
    OpenAIEmbeddingProvider,
    OpenAILLMProvider,
)

logger = structlog.get_logger(__name__)


def _wrap_with_tracing(provider: LLMProvider, run_name: str = "llm_generate") -> LLMProvider:
    """
    Wrap a raw LLMProvider with LangSmith tracing when enabled.
    Returns the provider unchanged if LangSmith is not configured.
    Gracefully no-ops if the evaluation package is unavailable.
    """
    try:
        from evaluation.langsmith_tracing import _langsmith_enabled, TracedLLMProvider
        if _langsmith_enabled():
            logger.info("langsmith_tracing_enabled", run_name=run_name)
            return TracedLLMProvider(
                provider=provider,
                run_name=run_name,
                tags=["applyforge"],
            )
    except Exception as exc:
        logger.debug("langsmith_tracing_unavailable", reason=str(exc))
    return provider


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    provider = settings.llm_provider.lower()
    if provider == "openai":
        logger.info("ai_provider_selected", provider="openai", capability="llm", model=settings.llm_model)
        raw = OpenAILLMProvider(
            api_key=settings.ai_api_key_value,
            model=settings.llm_model,
            timeout_seconds=settings.ai_request_timeout_seconds,
            max_retries=settings.ai_max_retries,
            retry_backoff_seconds=settings.ai_retry_backoff_seconds,
            base_url=settings.openai_base_url,
        )
        return _wrap_with_tracing(raw)

    if provider == "mock" and settings.ai_allow_mock_providers:
        logger.warning("ai_mock_provider_selected", capability="llm")
        return _wrap_with_tracing(MockLLMProvider(), run_name="mock_generate")

    raise AIProviderConfigError(
        "Invalid LLM provider configuration. Use LLM_PROVIDER=openai for runtime or set "
        "AI_ALLOW_MOCK_PROVIDERS=true with LLM_PROVIDER=mock for tests/dev."
    )


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    provider = settings.embedding_provider.lower()
    if provider == "openai":
        logger.info("ai_provider_selected", provider="openai", capability="embedding", model=settings.embedding_model)
        return OpenAIEmbeddingProvider(
            api_key=settings.ai_api_key_value,
            model=settings.embedding_model,
            timeout_seconds=settings.ai_request_timeout_seconds,
            max_retries=settings.ai_max_retries,
            retry_backoff_seconds=settings.ai_retry_backoff_seconds,
            base_url=settings.openai_base_url,
        )

    if provider == "mock" and settings.ai_allow_mock_providers:
        logger.warning("ai_mock_provider_selected", capability="embedding")
        return MockEmbeddingProvider()

    raise AIProviderConfigError(
        "Invalid embedding provider configuration. Use EMBEDDING_PROVIDER=openai for runtime or set "
        "AI_ALLOW_MOCK_PROVIDERS=true with EMBEDDING_PROVIDER=mock for tests/dev."
    )

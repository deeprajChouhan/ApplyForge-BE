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


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    provider = settings.llm_provider.lower()
    if provider == "openai":
        logger.info("ai_provider_selected", provider="openai", capability="llm", model=settings.llm_model)
        return OpenAILLMProvider(
            api_key=settings.ai_api_key_value,
            model=settings.llm_model,
            timeout_seconds=settings.ai_request_timeout_seconds,
            max_retries=settings.ai_max_retries,
            retry_backoff_seconds=settings.ai_retry_backoff_seconds,
            base_url=settings.openai_base_url,
        )

    if provider == "mock" and settings.ai_allow_mock_providers:
        logger.warning("ai_mock_provider_selected", capability="llm")
        return MockLLMProvider()

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

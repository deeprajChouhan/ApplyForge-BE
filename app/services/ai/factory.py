from app.core.config import settings
from app.services.ai.providers import (
    EmbeddingProvider,
    LLMProvider,
    MockEmbeddingProvider,
    MockLLMProvider,
    OpenAIEmbeddingProvider,
    OpenAILLMProvider,
)


def get_llm_provider() -> LLMProvider:
    if settings.llm_provider.lower() == "openai":
        return OpenAILLMProvider(api_key=settings.ai_api_key or "", model=settings.llm_model)
    return MockLLMProvider()


def get_embedding_provider() -> EmbeddingProvider:
    if settings.embedding_provider.lower() == "openai":
        return OpenAIEmbeddingProvider(api_key=settings.ai_api_key or "", model=settings.embedding_model)
    return MockEmbeddingProvider()


from app.core.config import settings
from app.services.ai.providers import EmbeddingProvider, LLMProvider, MockEmbeddingProvider, MockLLMProvider


def get_llm_provider() -> LLMProvider:
    return MockLLMProvider()


def get_embedding_provider() -> EmbeddingProvider:
    return MockEmbeddingProvider()

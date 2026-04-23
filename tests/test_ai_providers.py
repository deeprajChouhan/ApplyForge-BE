from pydantic import SecretStr
import pytest

from app.core.config import settings
from app.services.ai.exceptions import AIProviderConfigError, AIProviderResponseError
from app.services.ai.factory import get_embedding_provider, get_llm_provider
from app.services.ai.providers import MockLLMProvider, OpenAIEmbeddingProvider, OpenAILLMProvider


@pytest.fixture(autouse=True)
def reset_ai_settings():
    original = {
        "llm_provider": settings.llm_provider,
        "embedding_provider": settings.embedding_provider,
        "ai_allow_mock_providers": settings.ai_allow_mock_providers,
        "ai_api_key": settings.ai_api_key,
    }
    get_llm_provider.cache_clear()
    get_embedding_provider.cache_clear()
    yield
    settings.llm_provider = original["llm_provider"]
    settings.embedding_provider = original["embedding_provider"]
    settings.ai_allow_mock_providers = original["ai_allow_mock_providers"]
    settings.ai_api_key = original["ai_api_key"]
    get_llm_provider.cache_clear()
    get_embedding_provider.cache_clear()


def test_factory_blocks_mock_provider_when_not_explicitly_allowed():
    settings.llm_provider = "mock"
    settings.ai_allow_mock_providers = False

    with pytest.raises(AIProviderConfigError):
        get_llm_provider()


def test_factory_uses_mock_provider_when_explicitly_enabled():
    settings.llm_provider = "mock"
    settings.ai_allow_mock_providers = True

    provider = get_llm_provider()

    assert isinstance(provider, MockLLMProvider)


def test_factory_requires_openai_key_for_runtime_provider():
    settings.llm_provider = "openai"
    settings.ai_allow_mock_providers = False
    settings.ai_api_key = None

    with pytest.raises(AIProviderConfigError):
        get_llm_provider()


def test_openai_llm_provider_returns_message_content_from_client_stub():
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return type(
                        "Response",
                        (),
                        {"choices": [type("Choice", (), {"message": type("Message", (), {"content": "ok"})()})()]},
                    )()

    provider = OpenAILLMProvider(
        api_key="test",
        model="gpt-test",
        timeout_seconds=1.0,
        max_retries=0,
        retry_backoff_seconds=0.0,
        client=FakeClient(),
    )

    assert provider.generate("system", "user") == "ok"


def test_openai_embedding_provider_raises_on_empty_vectors():
    class FakeClient:
        class embeddings:
            @staticmethod
            def create(**kwargs):
                return type("Response", (), {"data": []})()

    provider = OpenAIEmbeddingProvider(
        api_key="test",
        model="embed-test",
        timeout_seconds=1.0,
        max_retries=0,
        retry_backoff_seconds=0.0,
        client=FakeClient(),
    )

    with pytest.raises(AIProviderResponseError):
        provider.embed("hello")



def test_ai_api_key_property_unwraps_secret_string():
    settings.ai_api_key = SecretStr("super-secret")

    assert settings.ai_api_key_value == "super-secret"

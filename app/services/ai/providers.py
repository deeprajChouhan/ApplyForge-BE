from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import hashlib
import time
from typing import Iterator

import structlog
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)

from app.services.ai.exceptions import (
    AIProviderConfigError,
    AIProviderResponseError,
    AIProviderTransientError,
)

logger = structlog.get_logger(__name__)


@dataclass
class StreamChunk:
    """One piece of a streamed reply. `usage` arrives once, on the last chunk, when the provider reports it."""
    text: str = ""
    usage: dict[str, int] | None = field(default=None)  # {"in": prompt_tokens, "out": completion_tokens}


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError

    def stream(self, system_prompt: str, user_prompt: str) -> Iterator[StreamChunk]:
        """Stream a reply. Default: one chunk from generate(), no usage (callers estimate)."""
        yield StreamChunk(text=self.generate(system_prompt, user_prompt))


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class MockLLMProvider(LLMProvider):
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        return f"[MOCK_GENERATION]\n{user_prompt[:1000]}"

    def stream(self, system_prompt: str, user_prompt: str) -> Iterator[StreamChunk]:
        text = self.generate(system_prompt, user_prompt)
        step = 24
        for i in range(0, len(text), step):
            yield StreamChunk(text=text[i : i + step])
        yield StreamChunk(usage={"in": max(1, len(user_prompt) // 4), "out": max(1, len(text) // 4)})


class MockEmbeddingProvider(EmbeddingProvider):
    def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [b / 255 for b in digest[:16]]


class OpenAILLMProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        retry_backoff_seconds: float,
        base_url: str | None = None,
        client: OpenAI | None = None,
    ):
        if not api_key:
            raise AIProviderConfigError("OpenAI API key is required for LLM provider")
        self.client = client or OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)
        self.model = model
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        def _request() -> str:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content if response.choices else None
            if not content:
                raise AIProviderResponseError("OpenAI returned an empty generation")
            return content

        return self._run_with_retries("chat.completions", _request)

    def stream(self, system_prompt: str, user_prompt: str) -> Iterator[StreamChunk]:
        """
        Streamed chat completion. Only opening the stream is retried; once text
        has been sent to the user a failure is raised, never silently replayed
        (a replay would duplicate output and double-charge tokens).
        Wire-compatible with Azure OpenAI (stream_options.include_usage).
        """
        def _open():
            return self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=True,
                stream_options={"include_usage": True},
            )

        response = self._run_with_retries("chat.completions.stream", _open)
        try:
            for event in response:
                choices = getattr(event, "choices", None) or []
                if choices:
                    delta = getattr(choices[0], "delta", None)
                    text = getattr(delta, "content", None) if delta is not None else None
                    if text:
                        yield StreamChunk(text=text)
                usage = getattr(event, "usage", None)
                if usage is not None:
                    yield StreamChunk(usage={"in": usage.prompt_tokens or 0, "out": usage.completion_tokens or 0})
        except (APITimeoutError, APIConnectionError, RateLimitError, APIError) as exc:
            raise AIProviderTransientError("OpenAI stream interrupted") from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def _run_with_retries(self, operation: str, func):
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return func()
            except (APITimeoutError, APIConnectionError, RateLimitError, APIError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    logger.error("ai_provider_retries_exhausted", provider="openai", operation=operation)
                    raise AIProviderTransientError(f"OpenAI {operation} failed after retries") from exc
                sleep_for = self.retry_backoff_seconds * (2 ** attempt)
                logger.warning(
                    "ai_provider_retry",
                    provider="openai",
                    operation=operation,
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                    sleep_seconds=sleep_for,
                )
                time.sleep(sleep_for)
            except AuthenticationError as exc:
                raise AIProviderConfigError("OpenAI authentication failed") from exc
            except BadRequestError as exc:
                raise AIProviderResponseError("OpenAI rejected chat completion request") from exc
        raise AIProviderTransientError(f"OpenAI {operation} failed") from last_error


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        retry_backoff_seconds: float,
        base_url: str | None = None,
        client: OpenAI | None = None,
    ):
        if not api_key:
            raise AIProviderConfigError("OpenAI API key is required for embedding provider")
        self.client = client or OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)
        self.model = model
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    def embed(self, text: str) -> list[float]:
        def _request() -> list[float]:
            response = self.client.embeddings.create(model=self.model, input=text)
            if not response.data:
                raise AIProviderResponseError("OpenAI returned no embedding vectors")
            return response.data[0].embedding

        return self._run_with_retries("embeddings", _request)

    def _run_with_retries(self, operation: str, func):
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return func()
            except (APITimeoutError, APIConnectionError, RateLimitError, APIError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    logger.error("ai_provider_retries_exhausted", provider="openai", operation=operation)
                    raise AIProviderTransientError(f"OpenAI {operation} failed after retries") from exc
                sleep_for = self.retry_backoff_seconds * (2 ** attempt)
                logger.warning(
                    "ai_provider_retry",
                    provider="openai",
                    operation=operation,
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                    sleep_seconds=sleep_for,
                )
                time.sleep(sleep_for)
            except AuthenticationError as exc:
                raise AIProviderConfigError("OpenAI authentication failed") from exc
            except BadRequestError as exc:
                raise AIProviderResponseError("OpenAI rejected embedding request") from exc
        raise AIProviderTransientError(f"OpenAI {operation} failed") from last_error

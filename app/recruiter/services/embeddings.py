"""
Embedding for recruiter matching.

Reuses the app's real embedding provider (`app.services.ai.factory`) whenever an
AI key is configured, so production matching runs on the same engine as the rest
of ApplyForge. Otherwise it falls back to a deterministic, offline bag-of-words
embedding — related texts land near each other, so semantic matching stays
meaningful in dev and tests without any external service. (The app's built-in
MockEmbeddingProvider hashes the whole string and isn't suitable for similarity,
which is why we provide this fallback here.)
"""
from __future__ import annotations

import hashlib
import math
import re

from app.core.config import settings

_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")
_MOCK_DIM = 256


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _mock_embed(text: str) -> list[float]:
    vec = [0.0] * _MOCK_DIM
    for tok in _tokenize(text):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        idx = h % _MOCK_DIM
        sign = 1.0 if (h >> 8) & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _use_real_provider() -> bool:
    return settings.embedding_provider.lower() == "openai" and bool(settings.ai_api_key_value)


def embed(text: str) -> list[float]:
    if _use_real_provider():
        try:
            from app.services.ai.factory import get_embedding_provider

            return get_embedding_provider().embed(text or " ")
        except Exception:
            # Never let a provider hiccup break matching — fall back offline.
            pass
    return _mock_embed(text)

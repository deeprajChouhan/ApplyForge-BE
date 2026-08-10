"""
Thin AI helper for the recruiter module.

Centralises the "is a real LLM configured?" gate and a JSON-returning wrapper so
CV parsing, advisory reasoning, and listing generation share one safe path.
Every call fails soft: a missing key, a mock provider, a provider hiccup, or
unparseable output all return None, and callers fall back to their deterministic
logic. Nothing here can break a request.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import settings


def llm_enabled() -> bool:
    """True only when a real OpenAI LLM is configured (not the mock)."""
    return settings.llm_provider.lower() == "openai" and bool(settings.ai_api_key_value)


def generate(system_prompt: str, user_prompt: str) -> str | None:
    """Run the shared LLM provider; return None on mock/empty/error."""
    if not llm_enabled():
        return None
    try:
        from app.services.ai.factory import get_llm_provider

        out = get_llm_provider().generate(system_prompt, user_prompt)
    except Exception:
        return None
    if not out or "[MOCK_GENERATION]" in out:
        return None
    return out


def generate_json(system_prompt: str, user_prompt: str) -> Any | None:
    """Generate and parse a JSON object/array; None if anything goes wrong."""
    out = generate(system_prompt, user_prompt)
    if not out:
        return None
    return _extract_json(out)


def _extract_json(text: str) -> Any | None:
    t = text.strip()
    # Strip Markdown code fences if the model wrapped the JSON.
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    # Try a direct parse first, then the widest brace/bracket span.
    try:
        return json.loads(t)
    except Exception:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = t.find(opener), t.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(t[i : j + 1])
            except Exception:
                continue
    return None

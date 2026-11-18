"""LLM price table (USD per 1,000 tokens) and cost estimation.

Single source of truth for both the runtime admin cost views and the offline
evaluation tooling (``evaluation/cost_tracker.py`` re-exports from here).
Prices are list prices — treat results as estimates, not invoices. When moving
to Azure OpenAI, check the contract rates and update this table.
"""
from __future__ import annotations

# Most-specific keys are matched first (gpt-4o-mini before gpt-4o).
OPENAI_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o-mini":                   {"prompt": 0.00015,  "completion": 0.0006},
    "gpt-4o":                        {"prompt": 0.005,    "completion": 0.015},
    "gpt-4-turbo":                   {"prompt": 0.01,     "completion": 0.03},
    "gpt-4":                         {"prompt": 0.03,     "completion": 0.06},
    "gpt-3.5-turbo-instruct":        {"prompt": 0.0015,   "completion": 0.002},
    "gpt-3.5-turbo":                 {"prompt": 0.0005,   "completion": 0.0015},
    "text-embedding-3-small":        {"prompt": 0.00002,  "completion": 0.0},
    "text-embedding-3-large":        {"prompt": 0.00013,  "completion": 0.0},
    "text-embedding-ada-002":        {"prompt": 0.0001,   "completion": 0.0},
    "claude-3-5-sonnet-20241022":    {"prompt": 0.003,    "completion": 0.015},
    "claude-3-haiku-20240307":       {"prompt": 0.00025,  "completion": 0.00125},
    "claude-3-opus-20240229":        {"prompt": 0.015,    "completion": 0.075},
}
_DEFAULT_PRICING: dict[str, float] = {"prompt": 0.002, "completion": 0.002}

# Models that never cost anything (offline/mock providers).
_FREE_MODEL_PREFIXES = ("mock",)


def is_priced(model: str) -> bool:
    """True if ``model`` matches a known price (False → default rate was used)."""
    m = (model or "").lower()
    return m.startswith(_FREE_MODEL_PREFIXES) or any(k in m for k in OPENAI_PRICING)


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """
    Estimate cost in USD for a single LLM call.
    Matches the longest key first so 'gpt-4o-mini' wins over 'gpt-4o'.
    """
    model_lower = (model or "").lower()
    if model_lower.startswith(_FREE_MODEL_PREFIXES):
        return 0.0
    pricing = _DEFAULT_PRICING
    for key in sorted(OPENAI_PRICING, key=len, reverse=True):
        if key in model_lower or model_lower.startswith(key):
            pricing = OPENAI_PRICING[key]
            break
    return round(
        (prompt_tokens / 1000) * pricing["prompt"]
        + (completion_tokens / 1000) * pricing["completion"],
        8,
    )

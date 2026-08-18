"""Async HTTP client utilities used by ATS pollers.

Provides a shared ``httpx.AsyncClient`` singleton, a per-host rate limiter
(via ``aiolimiter``), manual exponential-backoff retries, and a
``fetch_json`` convenience helper.

No network I/O is performed at import time — the underlying client and
limiters are created lazily on first use.
"""
from __future__ import annotations

import asyncio
import os
import random
from typing import Any, Optional

import httpx
from aiolimiter import AsyncLimiter

USER_AGENT = "ApplyForgeBot/1.0 (+https://applyforge.co.uk/bot)"

DEFAULT_RATE_LIMIT_PER_SEC = float(os.environ.get("RATE_LIMIT_DEFAULT", "10"))
DEFAULT_MAX_RETRIES = 3
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 30.0

_TIMEOUT = httpx.Timeout(connect=DEFAULT_CONNECT_TIMEOUT, read=DEFAULT_READ_TIMEOUT, write=10.0, pool=5.0)

_client: Optional[httpx.AsyncClient] = None
_client_lock: Optional[asyncio.Lock] = None
_limiters: dict[str, AsyncLimiter] = {}
_limiters_lock: Optional[asyncio.Lock] = None


def _get_client_lock() -> asyncio.Lock:
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


def _get_limiters_lock() -> asyncio.Lock:
    global _limiters_lock
    if _limiters_lock is None:
        _limiters_lock = asyncio.Lock()
    return _limiters_lock


def _rate_limit_for_host(host: str) -> float:
    """Read RATE_LIMIT_{HOST} env var (requests/sec), falling back to default.

    Host is normalised to an env-var-safe key, e.g. "boards-api.greenhouse.io"
    -> "RATE_LIMIT_BOARDS_API_GREENHOUSE_IO".
    """
    key = "RATE_LIMIT_" + "".join(
        c if c.isalnum() else "_" for c in host.upper()
    )
    value = os.environ.get(key)
    if value is None:
        return DEFAULT_RATE_LIMIT_PER_SEC
    try:
        return float(value)
    except ValueError:
        return DEFAULT_RATE_LIMIT_PER_SEC


async def get_shared_client() -> httpx.AsyncClient:
    """Return the process-wide ``httpx.AsyncClient`` singleton, creating it lazily."""
    global _client
    if _client is None:
        async with _get_client_lock():
            if _client is None:
                _client = httpx.AsyncClient(
                    timeout=_TIMEOUT,
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                )
    return _client


async def close_shared_client() -> None:
    """Close the shared client. Call this on application shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def get_limiter(host: str) -> AsyncLimiter:
    """Return (creating if necessary) the per-host rate limiter."""
    if host not in _limiters:
        async with _get_limiters_lock():
            if host not in _limiters:
                rate = _rate_limit_for_host(host)
                # AsyncLimiter(max_rate, time_period) => max_rate requests per time_period seconds.
                _limiters[host] = AsyncLimiter(max_rate=rate, time_period=1.0)
    return _limiters[host]


async def get_client(host: str) -> httpx.AsyncClient:
    """Return the shared client, ensuring a rate limiter exists for ``host``.

    Callers are expected to use the returned client together with
    ``get_limiter(host)`` (or simply use ``fetch_json``, which handles this
    automatically) to respect per-host rate limits.
    """
    await get_limiter(host)
    return await get_shared_client()


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


async def fetch_json(
    url: str,
    *,
    method: str = "GET",
    params: Optional[dict[str, Any]] = None,
    json_body: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """Fetch ``url`` and return parsed JSON, honouring per-host rate limits.

    Retries up to ``max_retries`` times with exponential backoff (plus
    jitter) on network errors, timeouts, HTTP 429, and 5xx responses.
    """
    parsed_host = httpx.URL(url).host
    limiter = await get_limiter(parsed_host)
    client = await get_shared_client()

    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)

    attempt = 0
    last_exc: Optional[Exception] = None

    while attempt < max_retries:
        attempt += 1
        try:
            async with limiter:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=request_headers,
                )

            if _is_retryable_status(response.status_code) and attempt < max_retries:
                await _sleep_backoff(attempt, response.headers.get("Retry-After"))
                continue

            response.raise_for_status()
            return response.json()

        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            if attempt >= max_retries:
                raise
            await _sleep_backoff(attempt, None)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"fetch_json: exhausted retries for {url} without a response")


async def _sleep_backoff(attempt: int, retry_after: Optional[str]) -> None:
    if retry_after is not None:
        try:
            delay = float(retry_after)
        except ValueError:
            delay = 2.0 ** attempt
    else:
        delay = 2.0 ** attempt
    delay += random.uniform(0, 0.5)
    await asyncio.sleep(delay)


__all__ = [
    "USER_AGENT",
    "get_shared_client",
    "close_shared_client",
    "get_limiter",
    "get_client",
    "fetch_json",
]

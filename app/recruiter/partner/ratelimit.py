"""
Fixed-window rate limiter.

Uses Redis when PARTNER_RATE_LIMIT_REDIS_URL is set (shared across replicas);
otherwise a per-process in-memory window. In multi-replica deployments set
the Redis URL (and keep the edge/WAF limit at Front Door) — the in-memory
limiter alone allows `limit × replicas` per window.
"""
from __future__ import annotations

import ipaddress
import threading
import time
from collections import OrderedDict
from functools import lru_cache

from app.core.config import settings

_lock = threading.Lock()
# bucket -> (window_start, window_seconds, count). Ordered for LRU eviction.
_buckets: "OrderedDict[str, tuple[int, int, int]]" = OrderedDict()
_MAX_BUCKETS = 200_000


def ip_bucket(ip: str | None) -> str:
    """Rate-limit identity for an IP: IPv6 is grouped by /64 (one subscriber)."""
    if not ip:
        return "unknown"
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    if addr.version == 6:
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


@lru_cache(maxsize=1)
def _redis():
    url = settings.partner_rate_limit_redis_url
    if not url:
        return None
    try:
        import redis

        return redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
    except Exception:  # pragma: no cover - optional dependency path
        return None


def _evict(now: int) -> None:
    """Drop buckets whose OWN window has ended; then LRU-trim to the cap."""
    if len(_buckets) <= _MAX_BUCKETS // 2:
        return
    for k in [k for k, (start, length, _) in _buckets.items() if start + length <= now]:
        _buckets.pop(k, None)
    if len(_buckets) <= _MAX_BUCKETS:
        return
    # Over the cap inside live windows (a flood): shed the shortest-window,
    # least-recently-used buckets first so a minute-scale flood can never
    # evict — and thereby reset — the long-window abuse limits.
    excess = len(_buckets) - int(_MAX_BUCKETS * 0.9)  # trim with headroom so a flood doesn't re-sort per request
    for k in sorted(_buckets, key=lambda k: _buckets[k][1])[:excess]:
        _buckets.pop(k, None)


def hit(bucket: str, limit: int, window_seconds: int) -> tuple[bool, int, int]:
    """Count one hit. Returns (allowed, remaining, reset_epoch_seconds)."""
    now = int(time.time())
    window = now - (now % window_seconds)
    reset = window + window_seconds
    r = _redis()
    if r is not None:
        try:
            k = f"af:rl:{bucket}:{window_seconds}:{window}"
            pipe = r.pipeline()
            pipe.incr(k)
            pipe.expire(k, window_seconds + 5)
            count = int(pipe.execute()[0])
            return count <= limit, max(0, limit - count), reset
        except Exception:
            pass  # fall back to the local window rather than failing open on Redis blips
    key = f"{bucket}|{window_seconds}"
    with _lock:
        start, length, count = _buckets.get(key, (window, window_seconds, 0))
        if start != window:
            start, count = window, 0
        count += 1
        _buckets[key] = (start, window_seconds, count)
        _buckets.move_to_end(key)
        _evict(now)
    return count <= limit, max(0, limit - count), reset


def reset_all() -> None:
    """Test helper."""
    with _lock:
        _buckets.clear()

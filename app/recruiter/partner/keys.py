"""
API key generation, hashing and scope rules.

Format:  afk_<env>_<8 hex id>_<secret>   (secret key, server-to-server)
         afp_<env>_<8 hex id>_<secret>   (publishable key, browser forms)

`key_prefix` = everything before the last underscore. It is public, unique and
the lookup handle. Only HMAC-SHA256(pepper, full_key) is stored; the plaintext
is returned exactly once at creation.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from functools import lru_cache

from app.core.config import settings

KIND_SECRET = "secret"
KIND_PUBLISHABLE = "publishable"
ENV_LIVE = "live"
ENV_TEST = "test"

# ── Scopes ──────────────────────────────────────────────────────────────
SCOPE_DESCRIPTIONS: dict[str, str] = {
    "jobs:read": "List and read the agency's jobs (roles)",
    "jobs:write": "Create, update, upsert and close jobs",
    "jobs:read_public": "Read open jobs for a careers page",
    "applications:read": "Read applications and their status",
    "applications:create": "Submit applications (careers-site form)",
    "applications:write": "Move pipeline stages and add notes",
    "candidates:read": "Read candidate profiles",
    "candidates:write": "Push candidates into the pool",
    "candidates:delete": "Erase candidates (GDPR requests)",
    "documents:read": "Download stored CVs and documents",
    "clients:read": "List clients",
    "clients:write": "Create and upsert clients",
    "matching:run": "Run AI matching and read shortlists",
    "usage:read": "Read this month's API usage",
}
ALL_SCOPES = frozenset(SCOPE_DESCRIPTIONS)

# A publishable key lives in a browser — it can never do more than this.
PUBLISHABLE_SCOPES = frozenset({"jobs:read_public", "applications:create"})

# A key narrowed to one client (an employer's HR portal) may hold at most these.
# Agency-wide powers (pool, matching, erasure, clients, pipeline moves) stay
# with the agency.
CLIENT_SCOPED_MAX = frozenset({
    "jobs:read", "jobs:write", "jobs:read_public",
    "applications:read", "applications:create", "documents:read",
})

# Reads of private data. Test-mode keys are denied these (they only dry-run).
PRIVATE_READ_SCOPES = frozenset({
    "jobs:read", "applications:read", "candidates:read", "documents:read",
    "clients:read", "matching:run", "usage:read",
})

DEFAULT_SECRET_SCOPES = sorted(ALL_SCOPES - {"candidates:delete"})
DEFAULT_CLIENT_SCOPES = sorted({"jobs:read", "jobs:write", "applications:read", "applications:create"})


def allowed_scopes(kind: str, client_scoped: bool) -> frozenset[str]:
    if kind == KIND_PUBLISHABLE:
        return PUBLISHABLE_SCOPES
    return CLIENT_SCOPED_MAX if client_scoped else ALL_SCOPES


@lru_cache(maxsize=1)
def _pepper() -> bytes:
    explicit = settings.partner_key_pepper.get_secret_value() if settings.partner_key_pepper else ""
    material = explicit or f"partner-api-key::{settings.secret_key}"
    return hashlib.sha256(material.encode()).digest()


def hash_key(full_key: str) -> str:
    return hmac.new(_pepper(), full_key.encode(), hashlib.sha256).hexdigest()


def generate_key(kind: str, environment: str) -> tuple[str, str, str]:
    """Return (full_key, key_prefix, last4)."""
    head = "afp" if kind == KIND_PUBLISHABLE else "afk"
    env = ENV_TEST if environment == ENV_TEST else ENV_LIVE
    prefix = f"{head}_{env}_{secrets.token_hex(4)}"
    secret = secrets.token_urlsafe(32)
    full = f"{prefix}_{secret}"
    return full, prefix, full[-4:]


def split_prefix(full_key: str) -> str | None:
    """Return the lookup prefix of a presented key, or None if malformed."""
    if not full_key or len(full_key) > 200:
        return None
    parts = full_key.split("_")
    if len(parts) < 4 or parts[0] not in ("afk", "afp") or parts[1] not in (ENV_LIVE, ENV_TEST):
        return None
    if len(parts[2]) != 8:
        return None
    return "_".join(parts[:3])


def verify(full_key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_key(full_key), stored_hash)

"""
Google OAuth helper.

Exchanges an authorization code for a Google ID token, then extracts the
user's email. Uses only the stdlib (urllib) so no extra dependencies are needed.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from app.core.config import settings


@dataclass
class GoogleUserInfo:
    email: str
    name: str | None
    picture: str | None
    google_id: str


def _read_google_credentials() -> tuple[str | None, str | None]:
    """
    Try three sources in order so the server works regardless of where
    uvicorn is launched from:
      1. pydantic-settings (reads .env via resolved path at import time)
      2. os.environ        (set directly in the shell / Docker / systemd)
      3. raw .env parse    (fallback when pydantic's path resolution fails)
    """
    client_id = settings.google_client_id or os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = settings.google_client_secret or os.environ.get("GOOGLE_CLIENT_SECRET")

    if client_id and client_secret:
        return client_id, client_secret

    # Last resort: walk up the tree and parse the first .env we find
    search_dirs = [
        Path(__file__).resolve().parent,           # services/auth/
        Path(__file__).resolve().parent.parent,    # services/
        Path(__file__).resolve().parent.parent.parent,  # app/
        Path(__file__).resolve().parent.parent.parent.parent,  # backend/
        Path.cwd(),
        Path.cwd().parent,
    ]
    for directory in search_dirs:
        env_path = directory / ".env"
        if env_path.exists():
            for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                # Strip optional surrounding quotes
                val = val.strip().strip('"').strip("'").rstrip("\r")
                if key == "GOOGLE_CLIENT_ID" and not client_id:
                    client_id = val or None
                elif key == "GOOGLE_CLIENT_SECRET" and not client_secret:
                    client_secret = val or None
            if client_id and client_secret:
                break  # found both, stop searching

    return client_id, client_secret


def exchange_code_for_user(code: str, redirect_uri: str) -> GoogleUserInfo:
    """
    Exchange a Google OAuth authorization code for user info.
    Reads credentials via _read_google_credentials() which tries multiple sources.
    """
    client_id, client_secret = _read_google_credentials()

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=501,
            detail=(
                "Google OAuth is not configured on this server. "
                "Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to your backend .env file."
            ),
        )

    # ── Step 1: exchange authorization code for tokens ──────────────────────
    token_payload = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()

    token_req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=token_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(token_req, timeout=10) as resp:
            token_data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(
            status_code=400,
            detail=f"Google token exchange failed: {raw}",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Google servers: {exc}")

    id_token = token_data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="Google did not return an ID token.")

    # ── Step 2: verify the ID token via Google's tokeninfo endpoint ──────────
    verify_url = (
        "https://oauth2.googleapis.com/tokeninfo"
        f"?id_token={urllib.parse.quote(id_token, safe='')}"
    )
    try:
        with urllib.request.urlopen(verify_url, timeout=10) as resp:
            info = json.loads(resp.read())
    except urllib.error.HTTPError:
        raise HTTPException(status_code=400, detail="Google ID token verification failed.")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not verify Google token: {exc}")

    if info.get("aud") != client_id:
        raise HTTPException(status_code=400, detail="Google token audience mismatch.")

    email = info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Google account has no email address.")

    if not info.get("email_verified", False):
        raise HTTPException(status_code=400, detail="Google email address is not verified.")

    return GoogleUserInfo(
        email=email,
        name=info.get("name"),
        picture=info.get("picture"),
        google_id=info.get("sub", ""),
    )

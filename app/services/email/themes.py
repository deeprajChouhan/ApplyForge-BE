"""Email themes, taken from the two frontends' design tokens.

consumer  → user-frontend/src/app/globals.css     (warm stone + violet #7c3aed)
recruiter → recruiter-frontend/src/app/globals.css (Sage #4A6D50 on #FAFBF8)
ops       → plain, human-looking mail for the support@/info@ mailbox
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Theme:
    key: str
    brand: str
    primary: str
    primary_text: str
    tint: str
    tint_text: str
    bg: str
    surface: str
    text: str
    muted: str
    border: str
    font: str
    radius: str
    logo_url: str | None = None
    app_url: str = ""


CONSUMER = Theme(
    key="consumer",
    brand="ApplyForge",
    primary="#7c3aed",
    primary_text="#ffffff",
    tint="#f1eaff",
    tint_text="#4c1d95",
    bg="#fafaf8",
    surface="#ffffff",
    text="#1c1917",
    muted="#78716c",
    border="#e7e5e4",
    font="'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    radius="10px",
)

RECRUITER = Theme(
    key="recruiter",
    brand="ApplyForge Recruiter",
    primary="#4a6d50",
    primary_text="#ffffff",
    tint="#e3ebe1",
    tint_text="#3f6345",
    bg="#fafbf8",
    surface="#ffffff",
    text="#1c211d",
    muted="#5c665d",
    border="#d7ddd3",
    font="-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    radius="12px",
)

OPS = Theme(
    key="ops",
    brand="ApplyForge",
    primary="#1c1917",
    primary_text="#ffffff",
    tint="#f5f5f4",
    tint_text="#1c1917",
    bg="#ffffff",
    surface="#ffffff",
    text="#1c1917",
    muted="#78716c",
    border="#e7e5e4",
    font="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    radius="6px",
)

THEMES = {t.key: t for t in (CONSUMER, RECRUITER, OPS)}

# Priority Score tier colours (Strong / Good / Low) — consumer score badge.
TIER_COLORS = {
    "strong": ("#dcfce7", "#166534"),
    "good": ("#fef3c7", "#92400e"),
    "low": ("#f5f5f4", "#57534e"),
}


def _valid_hex(v: str | None) -> bool:
    return bool(v) and v.startswith("#") and len(v) in (4, 7) and all(c in "0123456789abcdefABCDEF" for c in v[1:])


def agency_theme(name: str, primary_color: str | None, logo_url: str | None) -> Theme:
    """White-labelled recruiter theme for mail sent on an agency's behalf to candidates/clients."""
    t = replace(RECRUITER, brand=name or RECRUITER.brand)
    if _valid_hex(primary_color):
        t = replace(t, primary=primary_color, tint_text=primary_color)
    if logo_url and logo_url.startswith("https://"):
        t = replace(t, logo_url=logo_url)
    return t

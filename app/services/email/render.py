"""
Render a template definition + context into (subject, html, text).

Template copy is admin-editable, so it goes through a *sandboxed* Jinja
environment with autoescape on: `{{ name }}` is HTML-escaped, attribute access
to private/unsafe members is blocked. Copy supports a tiny markdown subset:
blank-line paragraphs, `**bold**`, `[label](url)`, and `- ` bullet lines.

Components ("blocks") are produced in Python from structured context, so
admins choose *which* blocks appear but can't break their markup.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from typing import Any

from jinja2 import StrictUndefined, Undefined
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup, escape

from app.core.config import settings
from app.services.email.themes import TIER_COLORS, Theme


class _LenientUndefined(Undefined):
    """Missing variables render as empty rather than crashing a send."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return ""


_env_html = SandboxedEnvironment(autoescape=True, undefined=_LenientUndefined)
_env_text = SandboxedEnvironment(autoescape=False, undefined=_LenientUndefined)
_env_strict = SandboxedEnvironment(autoescape=True, undefined=StrictUndefined)


@dataclass
class Rendered:
    subject: str
    html: str
    text: str


# ── markdown-lite ──────────────────────────────────────────────────────────
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+|/[^)\s]*)\)")


def _inline(s: str, theme: Theme) -> str:
    s = _BOLD.sub(r"<strong>\1</strong>", s)
    return _LINK.sub(
        lambda m: f'<a href="{_abs_url(m.group(2), theme)}" style="color:{theme.primary};text-decoration:underline">{m.group(1)}</a>',
        s,
    )


def _abs_url(url: str, theme: Theme) -> str:
    if url.startswith("/") and theme.app_url:
        return theme.app_url.rstrip("/") + url
    return url


def md_to_html(src: str, theme: Theme) -> str:
    """`src` is already Jinja-rendered with autoescape, so user values are escaped."""
    out: list[str] = []
    p_style = f"margin:0 0 16px;font-size:15px;line-height:24px;color:{theme.text}"
    for block in re.split(r"\n\s*\n", src.strip()):
        lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        if all(ln.lstrip().startswith("- ") for ln in lines):
            items = "".join(
                f'<li style="margin:0 0 6px">{_inline(ln.lstrip()[2:], theme)}</li>' for ln in lines
            )
            out.append(f'<ul style="margin:0 0 16px;padding-left:20px;font-size:15px;line-height:24px;color:{theme.text}">{items}</ul>')
        else:
            out.append(f'<p style="{p_style}">{_inline("<br>".join(lines), theme)}</p>')
    return "".join(out)


def md_to_text(src: str) -> str:
    s = _BOLD.sub(r"\1", src)
    return _LINK.sub(r"\1 (\2)", s).strip()


# ── components ─────────────────────────────────────────────────────────────
def _e(v: Any) -> str:
    return str(escape("" if v is None else v))


def _score_badge(score: Any, tier: str | None) -> str:
    bg, fg = TIER_COLORS.get((tier or "").lower(), TIER_COLORS["low"])
    label = f"{_e(score)}" + (f" · {_e(tier.title())}" if tier else "")
    return (f'<span style="display:inline-block;padding:2px 10px;border-radius:999px;background:{bg};'
            f'color:{fg};font-size:12px;font-weight:600">{label}</span>')


def block_job_cards(ctx: dict, t: Theme) -> str:
    rows = []
    for j in (ctx.get("jobs") or [])[:10]:
        badge = _score_badge(j.get("score"), j.get("tier")) if j.get("score") is not None else ""
        title = _e(j.get("title"))
        if j.get("url"):
            title = f'<a href="{_e(_abs_url(j["url"], t))}" style="color:{t.text};text-decoration:none">{title}</a>'
        rows.append(
            f'<tr><td style="padding:14px 16px;border:1px solid {t.border};border-radius:{t.radius};background:{t.surface}">'
            f'<div style="font-size:15px;font-weight:600;color:{t.text}">{title}</div>'
            f'<div style="font-size:13px;color:{t.muted};margin:2px 0 8px">{_e(j.get("company"))}'
            f'{" · " + _e(j.get("location")) if j.get("location") else ""}</div>{badge}</td></tr>'
            f'<tr><td style="height:8px"></td></tr>'
        )
    return f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 16px">{"".join(rows)}</table>' if rows else ""


def block_candidate_cards(ctx: dict, t: Theme) -> str:
    rows = []
    for c in (ctx.get("candidates") or [])[:10]:
        name = _e(c.get("name"))
        if c.get("url"):
            name = f'<a href="{_e(_abs_url(c["url"], t))}" style="color:{t.text};text-decoration:none">{name}</a>'
        match = (f'<span style="float:right;font-size:13px;font-weight:700;color:{t.primary}">{_e(c.get("match"))}%</span>'
                 if c.get("match") is not None else "")
        reasons = "".join(f'<div style="font-size:13px;color:{t.text}">✓ {_e(r)}</div>' for r in (c.get("reasons") or [])[:3])
        gaps = "".join(f'<div style="font-size:13px;color:{t.muted}">– {_e(g)}</div>' for g in (c.get("gaps") or [])[:2])
        rows.append(
            f'<tr><td style="padding:14px 16px;border:1px solid {t.border};border-radius:{t.radius};background:{t.surface}">'
            f'{match}<div style="font-size:15px;font-weight:600;color:{t.text}">{name}</div>'
            f'<div style="font-size:13px;color:{t.muted};margin:2px 0 8px">{_e(c.get("headline"))}</div>{reasons}{gaps}</td></tr>'
            f'<tr><td style="height:8px"></td></tr>'
        )
    return f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 16px">{"".join(rows)}</table>' if rows else ""


def block_stats(ctx: dict, t: Theme) -> str:
    stats = (ctx.get("stats") or [])[:4]
    if not stats:
        return ""
    w = int(100 / len(stats))
    cells = "".join(
        f'<td width="{w}%" style="padding:12px;text-align:center;background:{t.tint};border-radius:{t.radius}">'
        f'<div style="font-size:22px;font-weight:700;color:{t.tint_text}">{_e(s.get("value"))}</div>'
        f'<div style="font-size:12px;color:{t.muted}">{_e(s.get("label"))}</div></td>'
        for s in stats
    )
    return f'<table role="presentation" width="100%" cellspacing="6" cellpadding="0" style="margin:0 0 16px"><tr>{cells}</tr></table>'


def block_table(ctx: dict, t: Theme) -> str:
    tbl = ctx.get("table") or {}
    cols, rows = tbl.get("columns") or [], (tbl.get("rows") or [])[:25]
    if not cols or not rows:
        return ""
    th = "".join(f'<th align="left" style="padding:8px;font-size:12px;color:{t.muted};border-bottom:1px solid {t.border}">{_e(c)}</th>' for c in cols)
    trs = "".join(
        "<tr>" + "".join(f'<td style="padding:8px;font-size:13px;color:{t.text};border-bottom:1px solid {t.border}">{_e(v)}</td>' for v in r) + "</tr>"
        for r in rows
    )
    return f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 16px;border-collapse:collapse"><tr>{th}</tr>{trs}</table>'


def block_callout(ctx: dict, t: Theme) -> str:
    c = ctx.get("callout") or {}
    if not c.get("text"):
        return ""
    tones = {"warning": ("#fffbeb", "#92400e", "#fcd34d"), "danger": ("#fef2f2", "#991b1b", "#fca5a5")}
    bg, fg, br = tones.get(c.get("tone"), (t.tint, t.tint_text, t.border))
    return (f'<div style="margin:0 0 16px;padding:12px 14px;border-radius:{t.radius};background:{bg};'
            f'border:1px solid {br};color:{fg};font-size:14px;line-height:22px">{_e(c["text"])}</div>')


def block_gaps(ctx: dict, t: Theme) -> str:
    """Truthful generation: unsupported claims / low-confidence fields are shown, never hidden."""
    gaps = ctx.get("unsupported_gaps") or []
    conf = ctx.get("parse_confidence")
    parts = []
    if conf is not None:
        parts.append(f'<div style="font-size:13px;color:{t.muted};margin-bottom:6px">Parse confidence: <strong style="color:{t.text}">{_e(conf)}%</strong></div>')
    if gaps:
        items = "".join(f'<li style="margin:0 0 4px">{_e(g)}</li>' for g in gaps[:8])
        parts.append(f'<div style="font-size:13px;color:{t.text}">Not backed by your resume — check before sending:</div>'
                     f'<ul style="margin:6px 0 0;padding-left:18px;font-size:13px;color:{t.text}">{items}</ul>')
    if not parts:
        return ""
    return f'<div style="margin:0 0 16px;padding:12px 14px;border:1px dashed {t.border};border-radius:{t.radius}">{"".join(parts)}</div>'


def block_quote(ctx: dict, t: Theme) -> str:
    q = ctx.get("quote")
    if not q:
        return ""
    return (f'<div style="margin:0 0 16px;padding:10px 14px;border-left:3px solid {t.primary};background:{t.tint};'
            f'font-size:14px;line-height:22px;color:{t.text};white-space:pre-wrap">{_e(q)}</div>')


BLOCKS = {
    "job_cards": block_job_cards,
    "candidate_cards": block_candidate_cards,
    "stats": block_stats,
    "table": block_table,
    "callout": block_callout,
    "gaps": block_gaps,
    "quote": block_quote,
}


# ── layout ─────────────────────────────────────────────────────────────────
_LAYOUT = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><meta name="supported-color-schemes" content="light">
<title>{{ subject }}</title></head>
<body style="margin:0;padding:0;background:{{ t.bg }};-webkit-text-size-adjust:100%">
<span style="display:none!important;visibility:hidden;opacity:0;height:0;width:0;overflow:hidden;mso-hide:all">{{ preheader }}</span>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{{ t.bg }}"><tr><td align="center" style="padding:32px 12px">
<table role="presentation" width="600" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px;font-family:{{ t.font }}">
{% if not plain %}
<tr><td style="padding:0 4px 20px">
{% if t.logo_url %}<img src="{{ t.logo_url }}" alt="{{ t.brand }}" height="32" style="height:32px;border:0;display:block">
{% else %}<table role="presentation" cellspacing="0" cellpadding="0"><tr>
<td style="width:28px;height:28px;background:{{ t.primary }};border-radius:8px;color:{{ t.primary_text }};font-size:13px;font-weight:800;text-align:center;vertical-align:middle">AF</td>
<td style="padding-left:10px;font-size:16px;font-weight:700;color:{{ t.text }}">{{ t.brand }}</td></tr></table>{% endif %}
</td></tr>{% endif %}
<tr><td style="background:{{ t.surface }};border:1px solid {{ t.border }};border-radius:{{ t.radius }};padding:{{ '28px' if not plain else '20px' }}">
{% if heading %}<h1 style="margin:0 0 16px;font-size:22px;line-height:30px;font-weight:700;color:{{ t.text }}">{{ heading }}</h1>{% endif %}
{{ body }}
{{ blocks }}
{% if cta_label and cta_url %}
<table role="presentation" cellspacing="0" cellpadding="0" style="margin:8px 0 4px"><tr><td style="border-radius:{{ t.radius }};background:{{ t.primary }}">
<a href="{{ cta_url }}" style="display:inline-block;padding:12px 22px;font-size:15px;font-weight:600;color:{{ t.primary_text }};text-decoration:none;border-radius:{{ t.radius }}">{{ cta_label }}</a>
</td></tr></table>
<p style="margin:12px 0 0;font-size:12px;line-height:18px;color:{{ t.muted }}">Button not working? Paste this into your browser:<br><span style="word-break:break-all">{{ cta_url }}</span></p>
{% endif %}
{% if signature %}<p style="margin:20px 0 0;font-size:14px;line-height:22px;color:{{ t.text }};white-space:pre-line">{{ signature }}</p>{% endif %}
</td></tr>
<tr><td style="padding:20px 4px 0;font-size:12px;line-height:18px;color:{{ t.muted }}">
{% if footer_reason %}<p style="margin:0 0 8px">{{ footer_reason }}</p>{% endif %}
{% if prefs_url %}<p style="margin:0 0 8px"><a href="{{ prefs_url }}" style="color:{{ t.muted }};text-decoration:underline">Email preferences</a>{% if unsubscribe_url %} · <a href="{{ unsubscribe_url }}" style="color:{{ t.muted }};text-decoration:underline">Unsubscribe</a>{% endif %}</p>{% endif %}
{% if legal %}<p style="margin:0 0 8px">{{ legal }}</p>{% endif %}
<p style="margin:0">{{ t.brand }} · <a href="{{ home_url }}" style="color:{{ t.muted }};text-decoration:none">{{ home_label }}</a></p>
</td></tr>
</table></td></tr></table></body></html>"""

_layout_tpl = _env_html.from_string(_LAYOUT)


def render_string(src: str | None, ctx: dict, *, html: bool) -> str:
    if not src:
        return ""
    env = _env_html if html else _env_text
    return env.from_string(src).render(**ctx)


def validate_template_source(*sources: str | None) -> None:
    """Raise if admin-edited copy has a syntax error (used on save in the admin UI)."""
    for s in sources:
        if s:
            _env_strict.parse(s)


def render(
    *,
    theme: Theme,
    subject: str,
    body: str,
    ctx: dict,
    preheader: str | None = None,
    heading: str | None = None,
    cta_label: str | None = None,
    cta_url: str | None = None,
    blocks: list[str] | None = None,
    footer_reason: str | None = None,
    prefs_url: str | None = None,
    unsubscribe_url: str | None = None,
    signature: str | None = None,
    plain: bool = False,
) -> Rendered:
    subj = render_string(subject, ctx, html=False).strip().replace("\n", " ")[:300]
    body_html = md_to_html(render_string(body, ctx, html=True), theme)
    body_text = md_to_text(render_string(body, ctx, html=False))
    block_html = "".join(BLOCKS[b](ctx, theme) for b in (blocks or []) if b in BLOCKS)
    cta_url_r = _abs_url(render_string(cta_url, ctx, html=False).strip(), theme) if cta_url else ""
    cta_label_r = render_string(cta_label, ctx, html=False).strip() if cta_label else ""
    heading_r = render_string(heading, ctx, html=False).strip() if heading else ""
    home = theme.app_url or f"https://{settings.email_domain}"
    out_html = _layout_tpl.render(
        t=theme,
        subject=subj,
        preheader=render_string(preheader, ctx, html=False) if preheader else "",
        heading=heading_r,
        body=Markup(body_html),
        blocks=Markup(block_html),
        cta_label=cta_label_r,
        cta_url=cta_url_r,
        footer_reason=footer_reason,
        prefs_url=prefs_url,
        unsubscribe_url=unsubscribe_url,
        legal=settings.email_company_legal or "",
        home_url=home,
        home_label=home.replace("https://", "").replace("http://", "").rstrip("/"),
        signature=signature,
        plain=plain,
    )
    text_parts = [heading_r, body_text]
    if cta_label_r and cta_url_r:
        text_parts.append(f"{cta_label_r}: {cta_url_r}")
    if signature:
        text_parts.append(signature)
    if unsubscribe_url:
        text_parts.append(f"Unsubscribe: {unsubscribe_url}")
    if settings.email_company_legal:
        text_parts.append(settings.email_company_legal)
    return Rendered(subject=subj, html=out_html, text="\n\n".join(p for p in text_parts if p))


def html_to_text(s: str) -> str:
    """Crude fallback for inbound mail with no text part (snippets / search)."""
    s = re.sub(r"(?is)<(script|style).*?</\1>", "", s or "")
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", _html.unescape(s)).strip()

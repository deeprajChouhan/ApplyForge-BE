"""
System resume-template definitions and config normalization.

The config schema is shared across the HTML preview renderer, PDF exporter
(ReportLab) and DOCX exporter (python-docx). Each renderer maps from this
same TemplateConfig — it is the single source of truth for section order,
which sections are shown, and the typography/spacing intent.

Schema (config_json):
{
  "version": 1,
  "layout": "single_column" | "two_column" | "centered",
  "sections": [
     { "key": "header",  "label": "Header",  "enabled": true, "order": 0 },
     ...
  ],
  "styles": {
     "font_family": str,
     "body_font_size": int,
     "heading_font_size": int,
     "line_height": float,
     "section_spacing": int,
     "margin_top": int, "margin_bottom": int,
     "margin_left": int, "margin_right": int,
     "accent_color": "#RRGGBB",
     "header_style": "left" | "centered"
  }
}

Section keys are a closed set — renderers dispatch on them; unknown keys
are dropped by normalize_config().
"""
from __future__ import annotations
from typing import Any


CLASSIC_KEY = "classic"

ALLOWED_BASE_TEMPLATES = {"classic", "modern", "sidebar", "executive"}
ALLOWED_LAYOUTS = {"single_column", "two_column", "centered"}
ALLOWED_SECTION_KEYS = [
    "header", "summary", "skills", "experience",
    "projects", "education", "certifications",
]

DEFAULT_SECTIONS: list[dict[str, Any]] = [
    {"key": "header",         "label": "Header",                "enabled": True, "order": 0},
    {"key": "summary",        "label": "Professional Summary",  "enabled": True, "order": 1},
    {"key": "skills",         "label": "Skills",                "enabled": True, "order": 2},
    {"key": "experience",     "label": "Work Experience",       "enabled": True, "order": 3},
    {"key": "projects",       "label": "Projects",              "enabled": True, "order": 4},
    {"key": "education",      "label": "Education",             "enabled": True, "order": 5},
    {"key": "certifications", "label": "Certifications",        "enabled": True, "order": 6},
]

DEFAULT_STYLES: dict[str, Any] = {
    "font_family": "Helvetica",
    "body_font_size": 10,
    "heading_font_size": 12,
    "line_height": 1.35,
    "section_spacing": 8,
    "margin_top": 40,
    "margin_bottom": 40,
    "margin_left": 46,
    "margin_right": 46,
    "accent_color": "#1D4ED8",
    "header_style": "left",
}


def build_default_config(layout: str = "single_column",
                         style_overrides: dict[str, Any] | None = None,
                         sections: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    styles = dict(DEFAULT_STYLES)
    if style_overrides:
        styles.update(style_overrides)
    return {
        "version": 1,
        "layout": layout if layout in ALLOWED_LAYOUTS else "single_column",
        "sections": [dict(s) for s in (sections or DEFAULT_SECTIONS)],
        "styles": styles,
    }


# Base configs for the four seeded system templates. name + base_template
# are kept identical to what the frontend previously hardcoded so URLs and
# UI copy don't shift.
SYSTEM_TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "Classic ATS",
        "base_template": "classic",
        "config": build_default_config("single_column"),
    },
    {
        "name": "Modern",
        "base_template": "modern",
        "config": build_default_config(
            "single_column",
            {"accent_color": "#0F766E", "font_family": "Inter"},
        ),
    },
    {
        "name": "Two-Column Sidebar",
        "base_template": "sidebar",
        "config": build_default_config(
            "two_column",
            {"accent_color": "#1E3A5F"},
        ),
    },
    {
        "name": "Executive",
        "base_template": "executive",
        "config": build_default_config(
            "centered",
            {"font_family": "Georgia", "accent_color": "#0F172A", "heading_font_size": 13},
        ),
    },
]


def _coerce_number(v: Any, default: float | int, minimum: float | int, maximum: float | int) -> float | int:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    n = max(minimum, min(maximum, n))
    # Preserve int-ness for pt/px sizes
    if isinstance(default, int):
        return int(round(n))
    return n


def _valid_hex_color(v: Any) -> bool:
    if not isinstance(v, str):
        return False
    if not v.startswith("#") or len(v) not in (4, 7):
        return False
    try:
        int(v[1:], 16)
        return True
    except ValueError:
        return False


def normalize_config(raw: Any) -> dict[str, Any]:
    """
    Turn any user-supplied config payload into the canonical shape all
    renderers accept. Silently drops unknown keys, clamps sizes, and
    guarantees every allowed section is present exactly once (missing ones
    are appended, disabled, at the end).

    This is the ONLY place where we accept user template input — call it
    from every write path.
    """
    if not isinstance(raw, dict):
        raw = {}

    layout = raw.get("layout")
    if layout not in ALLOWED_LAYOUTS:
        layout = "single_column"

    # Sections — normalize each, then union with defaults.
    provided = raw.get("sections") or []
    if not isinstance(provided, list):
        provided = []

    by_key: dict[str, dict[str, Any]] = {}
    order_counter = 0
    for item in provided:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if key not in ALLOWED_SECTION_KEYS or key in by_key:
            continue
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            label = next((s["label"] for s in DEFAULT_SECTIONS if s["key"] == key), key.title())
        else:
            label = label.strip()[:80]
        enabled = bool(item.get("enabled", True))
        raw_order = item.get("order", order_counter)
        try:
            order = int(raw_order)
        except (TypeError, ValueError):
            order = order_counter
        by_key[key] = {"key": key, "label": label, "enabled": enabled, "order": order}
        order_counter += 1

    # Ensure every allowed section is represented (disabled by default when missing)
    for default in DEFAULT_SECTIONS:
        if default["key"] not in by_key:
            by_key[default["key"]] = {
                "key": default["key"],
                "label": default["label"],
                "enabled": False,
                "order": order_counter,
            }
            order_counter += 1

    sections = sorted(by_key.values(), key=lambda s: (s["order"], ALLOWED_SECTION_KEYS.index(s["key"])))
    # Re-normalize order to 0..n-1
    for i, s in enumerate(sections):
        s["order"] = i

    raw_styles = raw.get("styles") if isinstance(raw.get("styles"), dict) else {}

    accent = raw_styles.get("accent_color")
    if not _valid_hex_color(accent):
        accent = DEFAULT_STYLES["accent_color"]

    header_style = raw_styles.get("header_style")
    if header_style not in ("left", "centered"):
        header_style = DEFAULT_STYLES["header_style"]

    font_family = raw_styles.get("font_family")
    if not isinstance(font_family, str) or not font_family.strip():
        font_family = DEFAULT_STYLES["font_family"]
    font_family = font_family.strip()[:60]

    styles = {
        "font_family":       font_family,
        "body_font_size":    _coerce_number(raw_styles.get("body_font_size"),    DEFAULT_STYLES["body_font_size"],    7, 14),
        "heading_font_size": _coerce_number(raw_styles.get("heading_font_size"), DEFAULT_STYLES["heading_font_size"], 8, 20),
        "line_height":       _coerce_number(raw_styles.get("line_height"),       DEFAULT_STYLES["line_height"],       1.0, 2.0),
        "section_spacing":   _coerce_number(raw_styles.get("section_spacing"),   DEFAULT_STYLES["section_spacing"],   0, 40),
        "margin_top":        _coerce_number(raw_styles.get("margin_top"),        DEFAULT_STYLES["margin_top"],        10, 120),
        "margin_bottom":     _coerce_number(raw_styles.get("margin_bottom"),     DEFAULT_STYLES["margin_bottom"],     10, 120),
        "margin_left":       _coerce_number(raw_styles.get("margin_left"),       DEFAULT_STYLES["margin_left"],       10, 120),
        "margin_right":      _coerce_number(raw_styles.get("margin_right"),      DEFAULT_STYLES["margin_right"],      10, 120),
        "accent_color":      accent,
        "header_style":      header_style,
    }

    return {
        "version": 1,
        "layout": layout,
        "sections": sections,
        "styles": styles,
    }

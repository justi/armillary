"""Shared design-system CSS + HTML component helpers.

Implements the Claude Design bundle spec (dark default, status colors,
typography tokens). Injected once per rerender via ``inject_css()``.
The HTML builders (``status_chip``, ``big_suggestion_card`` etc.) return
plain strings suitable for ``st.markdown(..., unsafe_allow_html=True)``
or ``st.html(...)``.

Keep this module Streamlit-agnostic at the helper level (pure string
builders) so it can be unit-tested without a running Streamlit session.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import streamlit as st


def _load_dashboard_css() -> str:
    """Read the sibling ``dashboard.css`` at import time.

    Falls back to an empty string (no custom styles) when the file is
    missing — e.g. a broken package install or running from an
    environment that stripped non-Python assets. Better to ship a
    plain Streamlit dashboard than to blow up the entire UI import.
    """
    css_path = Path(__file__).with_name("dashboard.css")
    try:
        return css_path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.warn(
            f"Could not load dashboard CSS from {css_path}: {exc}. "
            "Continuing without custom dashboard styles.",
            RuntimeWarning,
            stacklevel=2,
        )
        return ""


# Extracted to a sibling .css file so the module stays readable and the
# design tokens are editable without touching Python. Loaded once at
# import time; GitHub / editors syntax-highlight the .css properly.
_CSS = _load_dashboard_css()

STATUS_COLORS = {
    "ACTIVE": "#40c463",
    "STALLED": "#f0ad4e",
    "DORMANT": "#666666",
    "IDEA": "#a371f7",
    "IN_PROGRESS": "#a371f7",
    "ARCHIVED": "#8b949e",
}

SPARK_COLOR_RISING = "#40c463"
SPARK_COLOR_FALLING = "#f0ad4e"
SPARK_COLOR_DEAD = "#8b949e"
SPARK_COLOR_NEUTRAL = "#c9d1d9"

# Semantic accent colors — referenced by both CSS tokens and Python
# builders, so the strip / chip / banner code never hardcodes hex.
ACCENT_WARNING = STATUS_COLORS["STALLED"]  # #f0ad4e — amber
ACCENT_DANGER = "#f85149"  # matches --arm-danger in the CSS
ACCENT_FORGOTTEN = STATUS_COLORS["IDEA"]  # #a371f7 — purple


def inject_css() -> None:
    """Inject design-system CSS once per Streamlit rerender.

    Safe to call from every view's render function — Streamlit dedupes
    identical HTML blocks via its internal element tree, and the cost
    is negligible even if it didn't.
    """
    st.markdown(_CSS, unsafe_allow_html=True)


# --- pure HTML builders (testable without Streamlit) ------------------------


def status_chip(status: str) -> str:
    """Pill-shaped status badge — colored dot + uppercase label."""
    color = STATUS_COLORS.get(status, "#8b949e")
    bg = f"{color}22"  # ~13% alpha in hex
    return (
        f'<span class="arm-chip" style="background:{bg};color:{color};">'
        f'<span class="dot" style="background:{color};"></span>'
        f"{status}</span>"
    )


def status_dot(status: str) -> str:
    """Inline 8px colored dot used before project names in tables."""
    color = STATUS_COLORS.get(status, "#666")
    return f'<span class="arm-dot" style="background:{color};"></span>'


def purpose_quote(purpose: str) -> str:
    """Italic left-bordered quote for the detail-page purpose line."""
    import html

    return f'<div class="arm-purpose-quote">{html.escape(purpose)}</div>'


def glance_strip(cells: list[dict]) -> str:
    """At-a-glance 5-metric strip.

    Each cell dict: ``{"label": str, "value": str, "sub": str,
    "tone": "warning"|"danger"|None, "is_spark": bool}``.
    """
    import html

    parts = ['<div class="arm-glance">']
    for c in cells:
        tone = c.get("tone") or ""
        is_spark = c.get("is_spark")
        value_class = "arm-glance-value"
        if tone:
            value_class += f" {tone}"
        if is_spark:
            value_class += " spark"
        # value may be pre-rendered HTML (sparkline); label/sub always escaped
        value = c["value"] if is_spark else html.escape(str(c["value"]))
        parts.append(
            '<div class="arm-glance-cell">'
            f'<div class="arm-glance-label">{html.escape(c["label"])}</div>'
            f'<div class="{value_class}">{value}</div>'
            f'<div class="arm-glance-sub">{html.escape(c.get("sub", ""))}</div>'
            "</div>"
        )
    parts.append("</div>")
    return "".join(parts)


def status_strip_cell(
    *,
    icon: str,
    color: str,
    count: int,
    label: str,
    sub: str,
    tooltip: str | None = None,
) -> str:
    """One cell for the merged status strip (zombies/at-risk/forgotten)."""
    import html

    bg = f"{color}22"
    title_attr = f' title="{html.escape(tooltip, quote=True)}"' if tooltip else ""
    return (
        f'<div class="arm-strip-cell"{title_attr}>'
        f'<div class="arm-strip-icon" style="background:{bg};color:{color};">'
        f"{html.escape(icon)}</div>"
        '<div style="flex:1;">'
        f'<div><span class="arm-strip-num">{count}</span>'
        f'<span class="arm-strip-label">{html.escape(label)}</span></div>'
        f'<div class="arm-strip-sub">{html.escape(sub)}</div>'
        "</div></div>"
    )


def status_strip(cells_html: list[str]) -> str:
    """Grid wrapper for 1..3 status-strip cells."""
    return '<div class="arm-strip">' + "".join(cells_html) + "</div>"


def section_header(title: str, subtitle: str = "") -> str:
    """'Recent work' style section header with horizontal rule."""
    import html

    sub_html = f'<div class="sub">{html.escape(subtitle)}</div>' if subtitle else ""
    return (
        '<div class="arm-section">'
        f"<h3>{html.escape(title)}</h3>"
        '<div class="rule"></div>'
        f"{sub_html}</div>"
    )


def spark_color_for_trend(trend: str | None) -> str:
    """Pick sparkline color per velocity_trend."""
    if trend == "rising":
        return SPARK_COLOR_RISING
    if trend == "falling":
        return SPARK_COLOR_FALLING
    if trend == "dead":
        return SPARK_COLOR_DEAD
    return SPARK_COLOR_NEUTRAL


SPARK_CHARS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def sparkline_text(values: list[int]) -> str:
    """Unicode block-character sparkline (no HTML wrapper)."""
    if not values:
        return ""
    peak = max(values) or 1
    return "".join(SPARK_CHARS[min(int(v / peak * 7), 7)] for v in values)


def sparkline_html(
    values: list[int],
    *,
    color: str | None = None,
    trend: str | None = None,
    css_class: str = "",
) -> str:
    """Block-character sparkline wrapped in the shared ``.arm-spark`` span.

    Resolves color from ``color`` arg, or ``trend`` via
    ``spark_color_for_trend``. ``css_class`` adds tone modifiers
    (``"rising"``, ``"falling"``, ``"dead"``) for CSS-only styling.
    """
    text = sparkline_text(values)
    if not text:
        return ""
    resolved = color or spark_color_for_trend(trend)
    class_attr = f"arm-spark {css_class}".strip()
    return f'<span class="{class_attr}" style="color:{resolved};">{text}</span>'


def spark_color_for_status(status: str) -> str:
    """Pick sparkline color per status (for table rows)."""
    if status == "ACTIVE":
        return SPARK_COLOR_RISING
    if status == "STALLED":
        return SPARK_COLOR_FALLING
    return SPARK_COLOR_DEAD

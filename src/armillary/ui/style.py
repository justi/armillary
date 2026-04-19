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

import streamlit as st

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


_CSS = """
<style>
:root {
  --arm-bg: #0d1117;
  --arm-bg-soft: #161b22;
  --arm-border: #30363d;
  --arm-border-soft: #21262d;
  --arm-fg: #c9d1d9;
  --arm-fg-muted: #8b949e;
  --arm-fg-dim: #6e7781;
  --arm-accent: #58a6ff;
  --arm-status-active: #40c463;
  --arm-status-stalled: #f0ad4e;
  --arm-status-dormant: #666666;
  --arm-status-idea: #a371f7;
  --arm-status-archived: #8b949e;
  --arm-danger: #f85149;
  --arm-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

/* Hero "today's focus" kicker */
.arm-hero-kicker {
  font-size: 12px;
  color: var(--arm-fg-muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 4px;
}
.arm-hero h2, h2.arm-hero {
  font-size: 32px !important;
  font-weight: 700 !important;
  letter-spacing: -0.02em;
  margin: 0 0 4px !important;
}

/* Status chip — used in detail header */
.arm-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 16px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  vertical-align: middle;
}
.arm-chip .dot {
  width: 7px; height: 7px; border-radius: 50%;
  display: inline-block;
}

/* Status dot inline — used before names in tables */
.arm-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 10px;
  vertical-align: middle;
  flex-shrink: 0;
}

/* Italic purpose quote — detail header */
.arm-purpose-quote {
  font-size: 16px;
  color: var(--arm-fg);
  line-height: 1.5;
  border-left: 2px solid var(--arm-border);
  padding-left: 14px;
  margin: 4px 0 14px;
  font-style: italic;
}

/* At-a-glance strip */
.arm-glance {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 1px;
  background: var(--arm-border);
  border: 1px solid var(--arm-border);
  border-radius: 10px;
  overflow: hidden;
  margin: 4px 0 8px;
}
.arm-glance-cell {
  padding: 14px 16px;
  background: var(--arm-bg);
}
.arm-glance-label {
  font-size: 10px;
  color: var(--arm-fg-muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.arm-glance-value {
  font-size: 24px;
  font-weight: 700;
  margin-top: 4px;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  color: #f0f6fc;
}
.arm-glance-value.warning { color: var(--arm-status-stalled); }
.arm-glance-value.danger { color: var(--arm-danger); }
.arm-glance-value.spark { font-size: 20px; font-family: var(--arm-mono); }
.arm-glance-sub {
  font-size: 11px;
  color: var(--arm-fg-muted);
  margin-top: 2px;
}

/* Status strip — 3 cells replacing separate warning banners */
.arm-strip {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 12px;
  margin: 8px 0 16px;
}
.arm-strip-cell {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--arm-border);
  border-radius: 8px;
  background: var(--arm-bg);
}
.arm-strip-icon {
  width: 36px; height: 36px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
}
.arm-strip-num {
  font-size: 20px;
  font-weight: 700;
  line-height: 1;
  color: #f0f6fc;
  font-variant-numeric: tabular-nums;
}
.arm-strip-label {
  font-size: 13px;
  font-weight: 400;
  color: var(--arm-fg-muted);
  margin-left: 4px;
}
.arm-strip-sub {
  font-size: 12px;
  color: var(--arm-fg-muted);
  margin-top: 2px;
}

/* Big suggestion card — dominant number */
.arm-big {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: 24px;
  padding: 18px 20px;
  border: 1px solid var(--arm-border);
  border-left-width: 3px;
  border-radius: 8px;
  margin-bottom: 10px;
  align-items: center;
  background: var(--arm-bg);
}
.arm-big.momentum { border-left-color: var(--arm-status-active); }
.arm-big.zombie { border-left-color: var(--arm-status-stalled); }
.arm-big.gold { border-left-color: var(--arm-status-idea); }
.arm-big.archive { border-left-color: var(--arm-status-archived); }
.arm-big-number {
  font-size: 40px;
  font-weight: 700;
  line-height: 1;
  color: var(--arm-fg);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}
.arm-big-unit {
  font-size: 11px;
  color: var(--arm-fg-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-top: 6px;
  line-height: 1.3;
}
.arm-big-name {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.arm-big-purpose {
  font-size: 13px;
  color: var(--arm-fg-muted);
  font-style: italic;
  margin-bottom: 6px;
}
.arm-big-meta {
  font-size: 12px;
  color: var(--arm-fg-dim);
  font-family: var(--arm-mono);
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.arm-spark { font-family: var(--arm-mono); font-size: 13px; }
.arm-spark.rising { color: var(--arm-status-active); }
.arm-spark.falling { color: var(--arm-status-stalled); }
.arm-spark.dead { color: var(--arm-status-archived); }

/* Dirty banner — detail */
.arm-dirty-banner {
  padding: 14px 18px;
  background: rgba(240, 173, 78, 0.08);
  border: 1px solid rgba(240, 173, 78, 0.3);
  border-left: 3px solid var(--arm-status-stalled);
  border-radius: 8px;
  margin: 8px 0 12px;
}
.arm-dirty-banner .title {
  font-size: 14px; font-weight: 600; color: #f0f6fc;
}
.arm-dirty-banner .sub {
  font-size: 12px; color: var(--arm-fg-muted); margin-top: 2px;
}

/* Timeline — recent commits */
.arm-timeline {
  position: relative;
  padding-left: 18px;
  margin: 8px 0;
}
.arm-timeline:before {
  content: "";
  position: absolute;
  left: 5px;
  top: 8px;
  bottom: 8px;
  width: 1px;
  background: var(--arm-border);
}
.arm-timeline-item {
  position: relative;
  padding-bottom: 14px;
}
.arm-timeline-item .dot {
  position: absolute;
  left: -18px;
  top: 4px;
  width: 11px; height: 11px;
  border-radius: 50%;
  background: var(--arm-border);
  border: 2px solid var(--arm-bg);
}
.arm-timeline-item.latest .dot {
  background: var(--arm-status-stalled);
  box-shadow: 0 0 0 3px rgba(240, 173, 78, 0.25);
}
.arm-timeline-item .line {
  display: flex; gap: 10px; align-items: baseline; font-size: 14px;
}
.arm-timeline-item .sha {
  font-family: var(--arm-mono);
  font-size: 12px;
  color: var(--arm-accent);
  background: var(--arm-bg-soft);
  padding: 1px 6px;
  border-radius: 4px;
}
.arm-timeline-item .msg { color: var(--arm-fg); }
.arm-timeline-item .meta {
  font-size: 12px; color: var(--arm-fg-dim);
  margin-top: 3px; font-style: italic;
}

/* Danger zone — archive box */
.arm-danger-zone {
  margin-top: 24px;
  padding: 16px 18px;
  border: 1px solid rgba(248, 81, 73, 0.35);
  border-radius: 10px;
  background: rgba(248, 81, 73, 0.04);
}
.arm-danger-zone .kicker {
  font-size: 11px;
  color: var(--arm-danger);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 8px;
  font-weight: 600;
}

/* Section header with horizontal rule */
.arm-section {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin: 24px 0 10px;
}
.arm-section h3 {
  font-size: 18px !important;
  font-weight: 600 !important;
  margin: 0 !important;
  color: #f0f6fc;
  letter-spacing: -0.01em;
}
.arm-section .rule {
  flex: 1; height: 1px; background: var(--arm-border-soft);
}
.arm-section .sub {
  font-size: 12px; color: var(--arm-fg-muted);
}
</style>
"""


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
) -> str:
    """One cell for the merged status strip (zombies/at-risk/forgotten)."""
    import html

    bg = f"{color}22"
    return (
        '<div class="arm-strip-cell">'
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


def spark_color_for_status(status: str) -> str:
    """Pick sparkline color per status (for table rows)."""
    if status == "ACTIVE":
        return SPARK_COLOR_RISING
    if status == "STALLED":
        return SPARK_COLOR_FALLING
    return SPARK_COLOR_DEAD

"""Hero-section rendering: verdict headline, yesterday line, transitions,
and the big-number suggestion cards.

Splits out of ``overview.py`` to keep each view under the 400-line
architecture target. Pure functions here (``build_hero_headline``,
``_big_number_parts``, ``_big_suggestion_html``) remain unit-testable;
the ``_render_*`` entry points are called from ``overview.py``.
"""

from __future__ import annotations

import html as _html

import streamlit as st

from armillary.cache import Cache
from armillary.ui.helpers import _shorten_home
from armillary.ui.style import sparkline_html

_CATEGORY_ICONS = {
    "momentum": "🔥",
    "zombie": "⚠️",
    "forgotten_gold": "💎",
    "archive_candidate": "📦",
}
_CATEGORY_LABELS = {
    "momentum": "Momentum",
    "zombie": "Zombie — kill or ship?",
    "forgotten_gold": "Forgotten gold",
    "archive_candidate": "Archive this?",
}


def _render_transitions() -> None:
    """Show status transitions since last scan (ADR 0025)."""
    import contextlib

    with contextlib.suppress(Exception):
        from armillary.transition_service import consume_pending_transitions

        transitions = consume_pending_transitions()
        if not transitions:
            return

        icons = {
            "DORMANT": "\U0001f4a4",
            "STALLED": "\u26a0\ufe0f",
            "ACTIVE": "\U0001f525",
            "ARCHIVED": "\U0001f4e6",
        }
        lines = []
        for t in transitions[:5]:
            icon = icons.get(t["to_status"], "\u2022")
            lines.append(
                f"{icon} **{t['project']}** \u2192 {t['to_status']} "
                f"(was {t['from_status']})"
            )
        if len(transitions) > 5:
            lines.append(f"+{len(transitions) - 5} more")

        st.info("\n\n".join(lines), icon=":material/swap_vert:")


def _render_yesterday_line(suggestions: list) -> None:
    """One-liner: what you worked on yesterday."""
    from datetime import datetime, timedelta

    yesterday = datetime.now() - timedelta(days=1)
    start = yesterday.replace(hour=0, minute=0, second=0)
    end = start + timedelta(days=1)

    with Cache() as cache:
        projects = cache.list_projects()
    from armillary.exclude_service import filter_excluded

    projects = filter_excluded(projects)
    from armillary.status_override import filter_archived

    projects = filter_archived(projects)

    active = [
        p.name
        for p in projects
        if p.metadata
        and p.metadata.last_commit_ts
        and start <= p.metadata.last_commit_ts < end
    ]
    if active:
        names = ", ".join(active[:3])
        more = f" +{len(active) - 3}" if len(active) > 3 else ""
        st.caption(f"Yesterday: {names}{more}")


def build_hero_headline(suggestions: list) -> str:
    """Dynamic verdict headline from active suggestions.

    Harry Dry feedback: a question ("What should you work on today?") is
    too soft; devs want specificity. Turns the suggestion mix into a
    one-line verdict. Pure function — testable without Streamlit.
    """
    if not suggestions:
        return "Nothing urgent. Go ship."
    n = len(suggestions)
    has_zombie = any(s.category == "zombie" for s in suggestions)
    has_momentum = any(s.category == "momentum" for s in suggestions)
    has_gold = any(s.category == "forgotten_gold" for s in suggestions)
    word = "projects" if n != 1 else "project"

    if has_zombie and has_momentum:
        return f"{n} {word} calling. One dying."
    if has_zombie:
        return f"{n} {word} calling. One's on life support."
    if has_momentum and has_gold:
        return f"{n} {word} calling. One has momentum, one is forgotten gold."
    if has_momentum:
        return f"{n} {word} with momentum. Keep shipping."
    if has_gold:
        return f"{n} forgotten {word}. Revive or archive."
    return f"{n} {word} calling."


def _big_number_parts(suggestion) -> tuple[str, str]:
    """Extract a dominant headline + unit caption from a Suggestion.

    Pure function — operates on ``Suggestion.project.metadata`` only.
    """
    from datetime import datetime

    md = suggestion.project.metadata
    hours = (md.work_hours or 0) if md else 0
    last = md.last_commit_ts if md else None
    dirty = (md.dirty_count or 0) if md else 0
    days_since = None
    if last:
        days_since = int((datetime.now() - last).total_seconds() / 86400)

    cat = suggestion.category
    if cat == "momentum":
        if days_since is None or days_since <= 0:
            headline = "today"
            unit = "last commit \u00b7 keep shipping"
        else:
            headline = f"{days_since}d"
            dirty_note = f" \u00b7 {dirty} waiting" if dirty else ""
            unit = f"since last commit{dirty_note}"
    elif cat == "zombie":
        headline = f"{days_since or 0}d"
        dirty_note = f" \u00b7 {dirty} waiting" if dirty else ""
        unit = f"since last commit{dirty_note}"
    elif cat == "forgotten_gold":
        headline = f"{hours:.0f}h"
        months = max(1, round((days_since or 30) / 30))
        unit = f"invested \u00b7 dormant {months}mo"
    elif cat == "archive_candidate":
        headline = f"{hours:.0f}h"
        unit = "invested \u00b7 dead 4wk"
    else:
        headline = "\u2014"
        unit = suggestion.reason[:60]
    return headline, unit


def _big_suggestion_html(
    *,
    category: str,
    emoji: str,
    name: str,
    headline: str,
    unit: str,
    purpose: str,
    path: str,
    spark_html: str,
) -> str:
    """Dominant-number suggestion card HTML (see ui/style.py .arm-big)."""
    css_cat = {
        "momentum": "momentum",
        "zombie": "zombie",
        "forgotten_gold": "gold",
        "archive_candidate": "archive",
    }.get(category, "")
    purpose_html = (
        f'<div class="arm-big-purpose">{_html.escape(purpose)}</div>' if purpose else ""
    )
    meta_sep = '<span style="color:#30363d;">\u00b7</span>' if spark_html else ""
    return (
        f'<div class="arm-big {css_cat}">'
        "<div>"
        f'<div class="arm-big-number">{_html.escape(headline)}</div>'
        f'<div class="arm-big-unit">{_html.escape(unit)}</div>'
        "</div>"
        '<div style="min-width:0;">'
        f'<div class="arm-big-name">{emoji} '
        f"<span>{_html.escape(name)}</span></div>"
        f"{purpose_html}"
        '<div class="arm-big-meta">'
        f"<span>{_html.escape(path)}</span>"
        f"{meta_sep}{spark_html}"
        "</div></div></div>"
    )


def _render_next_suggestions() -> None:
    """Hero section — the reason you open armillary every morning."""
    from armillary.next_service import get_suggestions
    from armillary.purpose_service import get_purpose
    from armillary.utils import excerpt_one_liner

    suggestions = get_suggestions()
    if not suggestions:
        return

    headline = build_hero_headline(suggestions)
    st.markdown(
        '<div class="arm-hero-kicker">TODAY\u2019S FOCUS</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<h2 class="arm-hero">{_html.escape(headline)}</h2>',
        unsafe_allow_html=True,
    )

    _render_yesterday_line(suggestions)
    st.caption("Momentum · zombies · forgotten gold — pick one to open.")

    for s in suggestions:
        emoji = _CATEGORY_ICONS.get(s.category, "\u2022")
        path_str = _shorten_home(s.project.path)

        purpose = get_purpose(str(s.project.path))
        md = s.project.metadata
        purpose_line = ""
        if purpose:
            purpose_line = purpose
        elif md and md.readme_excerpt:
            purpose_line = excerpt_one_liner(md.readme_excerpt)

        spark_html = ""
        if md and md.monthly_commits and any(c > 0 for c in md.monthly_commits):
            css_class = {
                "momentum": "rising",
                "zombie": "falling",
                "forgotten_gold": "dead",
                "archive_candidate": "dead",
            }.get(s.category, "")
            spark_html = sparkline_html(
                md.monthly_commits,
                trend=getattr(md, "velocity_trend", None),
                css_class=css_class,
            )

        head, unit = _big_number_parts(s)
        card_html = _big_suggestion_html(
            category=s.category,
            emoji=emoji,
            name=s.project.name,
            headline=head,
            unit=unit,
            purpose=purpose_line,
            path=path_str,
            spark_html=spark_html,
        )
        col_card, col_open, col_skip = st.columns([7, 1, 1])
        with col_card:
            st.markdown(card_html, unsafe_allow_html=True)
        with col_open:
            if st.button(
                "Open",
                key=f"next_open_{s.project.name}",
                icon=":material/open_in_new:",
                type="primary",
            ):
                st.query_params["project"] = str(s.project.path)
                st.rerun()
        with col_skip:
            if st.button(
                "Skip",
                key=f"next_skip_{s.project.name}",
                icon=":material/skip_next:",
                type="tertiary",
            ):
                from armillary.next_service import skip_project

                skip_project(str(s.project.path))
                st.rerun()

    st.markdown("---")

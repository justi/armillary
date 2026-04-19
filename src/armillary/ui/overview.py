"""Overview page — redesigned per ADR 0015.

Hierarchy: Header (minimal) → Next suggestions (hero) → Dormant banner →
           Search → Table (4 columns, narrative style).
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from armillary.cache import Cache
from armillary.config import Config
from armillary.exclude_service import filter_excluded
from armillary.ui.helpers import (
    _STATUS_EMOJI,
    OverviewRow,
    _load_overview_rows,
    _safe_load_config,
    _shorten_home,
)
from armillary.ui.search import _render_search_section
from armillary.ui.sidebar import _render_sidebar
from armillary.ui.style import (
    ACCENT_DANGER,
    ACCENT_FORGOTTEN,
    ACCENT_WARNING,
    sparkline_html,
    status_strip,
    status_strip_cell,
)


def _render_overview() -> None:
    st.session_state["_arm_view"] = "overview"
    for key in list(st.session_state):
        if key.startswith("_archive_confirm_"):
            st.session_state.pop(key)

    _render_header()

    # Record weekly pulse snapshot on every dashboard load (idempotent per week)
    import contextlib

    with contextlib.suppress(Exception):
        from armillary.pulse_service import take_snapshot

        take_snapshot()

    cfg = _safe_load_config()
    rows = _load_overview_rows()
    rows = filter_excluded(rows)
    # Don't filter_archived here — let sidebar show ARCHIVED as filter option.
    # ARCHIVED is hidden by default via _apply_filters when no status is selected.

    filters = _render_sidebar(rows, cfg)

    if not rows:
        _render_empty_cache_state(cfg)
        return

    # Check if dormant explore mode is active
    dormant_explore = st.session_state.get("_dormant_explore", False)

    # Status transitions (ADR 0025)
    if not dormant_explore:
        _render_transitions()

    if not dormant_explore:
        _render_next_suggestions()

    # Merged status strip — replaces zombie/at-risk/forgotten separate banners
    if not dormant_explore:
        from armillary.next_service import get_suggestions

        suggested_paths = {str(s.project.path) for s in get_suggestions()}
        _render_status_strip(rows, exclude_paths=suggested_paths)

    # Dormant explore action + "at risk" details still exposed via expanders
    _render_dormant_banner(rows, exploring=dormant_explore)

    # Apply filters — dormant explore bypasses normal filter
    if dormant_explore:
        filtered = [r for r in rows if r.status_raw == "DORMANT"]
        filtered.sort(key=lambda r: r.work_hours or 0, reverse=True)
        total_hours = sum(r.work_hours or 0 for r in filtered)
        st.caption(f"{len(filtered)} dormant projects \u00b7 {total_hours:.0f}h total")
        if filtered:
            _render_table(filtered)
        else:
            st.warning("No dormant projects.")
    elif filters["status"]:
        # Explicit status selection — flat table
        filtered = apply_status_filter(rows, filters["status"])
        st.caption(f"{len(filtered)} projects")
        if filtered:
            _render_table(filtered)
        else:
            st.warning("No projects match the current filters.")
    else:
        # Default: all projects (except ARCHIVED) in time groups
        all_visible = [r for r in rows if r.status_raw != "ARCHIVED"]
        _render_time_grouped_tables(all_visible)

    # Portfolio tab — pulse + heatmap + search (design change #3).
    # Today tab is conditional: no point showing an empty placeholder
    # if nothing changed in the last 24h (panel feedback).
    today_rows = find_today_activity(rows)
    if today_rows:
        tab_today, tab_portfolio = st.tabs(["Today", "Portfolio"])
        with tab_today:
            _render_today_activity(rows)
        with tab_portfolio:
            _render_search_section([r for r in rows if r.status_raw != "ARCHIVED"], cfg)
            _render_pulse_section()
            _render_activity_heatmap()
    else:
        _render_search_section([r for r in rows if r.status_raw != "ARCHIVED"], cfg)
        _render_pulse_section()
        _render_activity_heatmap()


def _render_header() -> None:
    """Minimal header: logo + relative scan time."""
    st.title(":material/explore: armillary")

    parts = []
    try:
        with Cache() as cache:
            last_ts = cache.last_scan_time()
            count = len(cache.list_projects())
    except Exception:  # noqa: BLE001
        last_ts = None
        count = 0

    if last_ts:
        delta = datetime.now() - datetime.fromtimestamp(last_ts)
        minutes = int(delta.total_seconds() / 60)
        if minutes < 1:
            parts.append("Scanned just now")
        elif minutes < 60:
            parts.append(f"Scanned {minutes}m ago")
        else:
            hours = minutes // 60
            parts.append(f"Scanned {hours}h ago")

    if count:
        parts.append(f"{count} projects")

    if parts:
        st.caption(" · ".join(parts))


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


def _render_next_suggestions() -> None:
    """Hero section — the reason you open armillary every morning."""
    from armillary.next_service import get_suggestions
    from armillary.purpose_service import get_purpose

    suggestions = get_suggestions()
    if not suggestions:
        return

    # HERO kicker + dynamic verdict headline (design system fs-display).
    # Panel feedback: "what should you work on today?" is too polite.
    # Devs want a verdict, not a question.
    headline = build_hero_headline(suggestions)
    st.markdown(
        '<div class="arm-hero-kicker">TODAY\u2019S FOCUS</div>',
        unsafe_allow_html=True,
    )
    import html as _html

    st.markdown(
        f'<h2 class="arm-hero">{_html.escape(headline)}</h2>',
        unsafe_allow_html=True,
    )

    # Yesterday's activity — retention hook
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
            from armillary.utils import excerpt_one_liner

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

        headline, unit = _big_number_parts(s)
        card_html = _big_suggestion_html(
            category=s.category,
            emoji=emoji,
            name=s.project.name,
            headline=headline,
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
        # Headline: days since last commit (or "today")
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
    import html as _html

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


def _render_status_strip(
    rows: list[OverviewRow],
    *,
    exclude_paths: set[str] | None = None,
) -> None:
    """Merged 3-cell strip replacing separate zombie/at-risk/forgotten banners.

    Change #2 from the design handoff: one grid, three icons + numbers.
    """
    from datetime import datetime, timedelta

    cutoff = datetime.now() - timedelta(days=14)
    zombies = [
        r
        for r in rows
        if r.status_raw == "ACTIVE"
        and r.last_modified < cutoff
        and r.work_hours
        and r.work_hours > 10
    ]
    at_risk = find_at_risk_projects(rows, exclude_paths=exclude_paths)
    dormant = [r for r in rows if r.status_raw == "DORMANT"]

    if not zombies and not at_risk and not dormant:
        return

    dormant_hours = sum(r.work_hours or 0 for r in dormant)
    at_risk_hours = sum(r.work_hours or 0 for r in at_risk)

    cells = [
        status_strip_cell(
            icon="\u26a0\ufe0f",
            color=ACCENT_WARNING,
            count=len(zombies),
            label="zombies",
            sub="no commit 14+ days" if zombies else "none \u2014 nice",
            tooltip=(
                "ACTIVE projects with no commits in 14+ days. Decide: kill or ship?"
            ),
        ),
        status_strip_cell(
            icon="\U0001f4dd",
            color=ACCENT_DANGER,
            count=len(at_risk),
            label="at risk",
            sub=(f"{at_risk_hours:.0f}h uncommitted" if at_risk else "all committed"),
            tooltip="STALLED projects with uncommitted work. Push or lose hours.",
        ),
        status_strip_cell(
            icon="\U0001f4b0",
            color=ACCENT_FORGOTTEN,
            count=len(dormant),
            label="forgotten",
            sub=(f"{dormant_hours:.0f}h invested" if dormant else "none dormant"),
            tooltip="DORMANT projects — dev abandoned 30+ days. Revive or archive.",
        ),
    ]
    st.markdown(status_strip(cells), unsafe_allow_html=True)

    # Surface the action + details for at-risk projects (list under expander)
    if at_risk:
        with st.expander(
            f"Show {len(at_risk)} at-risk project{'s' if len(at_risk) > 1 else ''}",
            expanded=False,
        ):
            for r in at_risk:
                hours_s = f"{r.work_hours:.0f}h" if r.work_hours else "0h"
                dirty_label = f"{r.dirty} file{'s' if r.dirty > 1 else ''}"
                col_info, col_act = st.columns([4, 1])
                with col_info:
                    st.markdown(
                        f"**{r.name}** \u2014 {dirty_label} uncommitted "
                        f"\u00b7 {hours_s}"
                    )
                with col_act:
                    if st.button(
                        "Open",
                        key=f"strip_atrisk_{r.path}",
                        icon=":material/open_in_new:",
                    ):
                        st.query_params["project"] = r.path
                        st.rerun()


def _render_dormant_banner(rows: list[OverviewRow], *, exploring: bool) -> None:
    """Golden banner for forgotten projects, or success bar when exploring."""
    dormant = [r for r in rows if r.status_raw == "DORMANT"]
    if not dormant:
        return

    total_hours = sum(r.work_hours or 0 for r in dormant)

    if exploring:
        col_msg, col_btn = st.columns([4, 1])
        with col_msg:
            st.success(
                f"Showing {len(dormant)} dormant projects · "
                f"{total_hours:.0f}h invested",
                icon=":material/savings:",
            )
        with col_btn:
            if st.button(
                "Clear filter",
                icon=":material/close:",
                key="clear_dormant_explore",
            ):
                st.session_state["_dormant_explore"] = False
                st.rerun()
    else:
        # Compact Explore action — the count/hours are already in status strip
        _ = total_hours  # kept for clarity; strip displays the aggregate
        if st.button(
            f"Explore {len(dormant)} forgotten projects",
            icon=":material/savings:",
            key="explore_dormant",
            type="secondary",
        ):
            st.session_state["_dormant_explore"] = True
            st.rerun()


def _render_empty_cache_state(cfg: Config | None) -> None:
    """Friendly first-launch screen with scan button."""
    st.subheader("Cache is empty", anchor=False)
    st.write(
        "First time? Your config is ready. Click Scan filesystem now to "
        "discover your projects. This reads metadata from every git repo "
        "under your umbrella folders."
    )

    from armillary.ui.actions import run_scan_with_feedback

    can_scan = cfg is not None and bool(cfg.umbrellas)
    col_btn, col_help = st.columns([1, 2])
    with col_btn:
        if st.button(
            "Scan filesystem now",
            icon=":material/sync:",
            width="stretch",
            disabled=not can_scan,
            key="empty_state_scan",
        ):
            run_scan_with_feedback(cfg)
    with col_help:
        if can_scan:
            st.caption(
                f"Will scan: {', '.join(_shorten_home(u.path) for u in cfg.umbrellas)}"
            )
        else:
            st.warning(
                "No umbrellas configured. "
                "Run `armillary config --init` from your terminal."
            )


# Sparkline text/HTML lives in armillary.ui.style (sparkline_text/_html).


def apply_status_filter(
    rows: list[OverviewRow],
    selected: list[str],
) -> list[OverviewRow]:
    """Filter rows by status. No selection = ACTIVE + STALLED default.

    Pure function — testable without Streamlit.
    """
    if selected:
        return [r for r in rows if r.status_raw in selected]
    return [r for r in rows if r.status_raw in ("ACTIVE", "STALLED")]


def find_at_risk_projects(
    rows: list[OverviewRow],
    *,
    exclude_paths: set[str] | None = None,
) -> list[OverviewRow]:
    """STALLED + dirty + >10h = uncommitted work at risk.

    Pure function — testable without Streamlit.
    """
    skip = exclude_paths or set()
    return [
        r
        for r in rows
        if r.status_raw == "STALLED"
        and r.dirty
        and r.dirty > 0
        and r.work_hours
        and r.work_hours > 10
        and r.path not in skip
    ]


def find_today_activity(
    rows: list[OverviewRow],
    *,
    now: datetime | None = None,
) -> list[OverviewRow]:
    """Projects touched in the last 24h (commit or any filesystem mtime).

    Pure function — testable without Streamlit. Uses ``last_modified``
    which the scan stores as the max of commit timestamp + head mtime,
    so a dirty edit without a commit still counts.
    """
    from datetime import datetime as _dt
    from datetime import timedelta

    ref = now or _dt.now()
    cutoff = ref - timedelta(hours=24)
    touched = [r for r in rows if r.last_modified and r.last_modified >= cutoff]
    touched.sort(key=lambda r: r.last_modified, reverse=True)
    return touched


def _render_today_activity(rows: list[OverviewRow]) -> None:
    """Today tab content: projects modified in the last 24h."""
    touched = find_today_activity(rows)

    if not touched:
        st.caption(
            "Nothing changed in the last 24 hours. "
            "Switch to Portfolio for history, search, and the heatmap."
        )
        return

    dirty_count = sum(1 for r in touched if r.dirty and r.dirty > 0)
    sub = f"{len(touched)} project{'s' if len(touched) > 1 else ''}"
    if dirty_count:
        sub += f" \u00b7 {dirty_count} with uncommitted work"
    st.caption(sub)

    for r in touched:
        delta = datetime.now() - r.last_modified
        minutes = int(delta.total_seconds() / 60)
        if minutes < 60:
            when = "just now" if minutes < 1 else f"{minutes}m ago"
        else:
            when = f"{minutes // 60}h ago"

        dirty_note = ""
        if r.dirty and r.dirty > 0:
            s = "s" if r.dirty > 1 else ""
            dirty_note = f" \u00b7 **{r.dirty} uncommitted file{s}**"

        emoji = _STATUS_EMOJI.get(r.status_raw, "\u00b7")
        col_info, col_open = st.columns([6, 1])
        with col_info:
            st.markdown(f"{emoji} **{r.name}** \u2014 {when}{dirty_note}")
        with col_open:
            if st.button(
                "Open",
                key=f"today_open_{r.path}",
                icon=":material/open_in_new:",
            ):
                st.query_params["project"] = r.path
                st.rerun()


def group_by_time(
    rows: list[OverviewRow],
) -> dict[str, list[OverviewRow]]:
    """Split rows into 'last_month', 'last_year', 'older'.

    Pure function — testable without Streamlit.
    """
    from datetime import datetime, timedelta

    now = datetime.now()
    month_ago = now - timedelta(days=30)
    year_ago = now - timedelta(days=365)
    return {
        "last_month": [r for r in rows if r.last_modified >= month_ago],
        "last_year": [r for r in rows if month_ago > r.last_modified >= year_ago],
        "older": [r for r in rows if r.last_modified < year_ago],
    }


def _apply_filters(
    rows: list[OverviewRow],
    *,
    filters: dict[str, list[str] | str],
) -> list[OverviewRow]:
    return apply_status_filter(rows, filters["status"])


def _render_pulse_section() -> None:
    """Weekly pulse — what changed this week (ADR 0018)."""
    from armillary.pulse_service import generate_pulse

    pulse = generate_pulse()
    if not pulse.worked_on and not pulse.went_dormant and not pulse.aging_wip:
        return

    total_entries = (
        len(pulse.worked_on) + len(pulse.went_dormant) + len(pulse.aging_wip)
    )
    with st.expander(
        f"Weekly pulse ({total_entries} updates)",
        icon=":material/monitoring:",
        expanded=False,
    ):
        if pulse.worked_on:
            st.markdown("**Worked on this week**")
            for e in pulse.worked_on:
                st.markdown(f"- {e.icon} **{e.project_name}** \u2014 {e.message}")
        if pulse.went_dormant:
            st.markdown("**Went dormant**")
            for e in pulse.went_dormant:
                st.markdown(f"- {e.icon} **{e.project_name}** \u2014 {e.message}")
        if pulse.aging_wip:
            st.markdown("**Uncommitted work**")
            for e in pulse.aging_wip:
                st.markdown(f"- {e.icon} **{e.project_name}** \u2014 {e.message}")

        # Pulse history chart (ADR 0022 M1)
        from armillary.pulse_service import load_history

        history = load_history()
        if len(history) < 2:
            st.caption(
                f"Portfolio chart appears after 2 weeks ({len(history)}/2 recorded)"
            )
        if len(history) >= 2:
            import pandas as pd

            st.markdown("---")
            st.markdown("**Portfolio evolution**")
            df = pd.DataFrame(history)
            df["date"] = pd.to_datetime(df["date"])
            st.area_chart(
                df.set_index("date")[["active", "stalled", "dormant"]],
                color=["#40c463", "#f0ad4e", "#666666"],
            )


def _render_activity_heatmap() -> None:
    """GitHub-contributions-style heatmap (ADR 0020)."""
    with st.expander(
        "Activity heatmap (12 months)",
        icon=":material/calendar_month:",
        expanded=False,
    ):
        from armillary.heatmap_service import daily_activity, heatmap_summary

        activity = daily_activity()
        if not activity:
            st.caption("No commit activity in the last 12 months.")
            return

        summary = heatmap_summary(activity)

        # Summary metrics
        cols = st.columns(4)
        with cols[0]:
            st.metric("Commits", f"{summary['total_commits']:,}")
        with cols[1]:
            st.metric("Active days", summary["active_days"])
        with cols[2]:
            st.metric("Longest streak", f"{summary['longest_streak']}d")
        with cols[3]:
            if summary["busiest_day"]:
                st.metric(
                    "Busiest day",
                    f"{summary['busiest_count']}",
                    help=str(summary["busiest_day"]),
                )

        # Heatmap — CSS grid with aspect-ratio: 1 for perfect squares
        from datetime import date, timedelta

        today = date.today()
        start = today - timedelta(days=364)

        # Build grid data: 53 weeks × 7 days
        peak = max(activity.values()) if activity else 1
        cells: list[str] = []
        for week in range(53):
            for day in range(7):
                d = start + timedelta(days=week * 7 + day)
                if d > today:
                    cells.append(
                        '<div class="hm-cell" style="visibility:hidden"></div>'
                    )
                    continue
                count = activity.get(d, 0)
                if count == 0:
                    color = "#ebedf0"
                else:
                    # 4 green levels like GitHub
                    level = min(int(count / peak * 4), 3)
                    color = ["#9be9a8", "#40c463", "#30a14e", "#216e39"][level]
                tip = f"{d.isoformat()}: {count} commits"
                cells.append(
                    f'<div class="hm-cell" style="background:{color}" '
                    f'title="{tip}"></div>'
                )

        html = (
            "<style>"
            ".hm-grid{display:grid;"
            "grid-template-rows:repeat(7,1fr);"
            "grid-auto-flow:column;"
            "gap:2px;width:100%}"
            ".hm-cell{aspect-ratio:1;border-radius:2px;"
            "min-width:2px;min-height:2px}"
            "</style>"
            '<div class="hm-grid">' + "".join(cells) + "</div>"
        )
        st.html(html)

        # Export button
        from armillary.heatmap_service import export_heatmap_html

        card_html = export_heatmap_html(activity, summary)
        st.download_button(
            "Download heatmap (HTML)",
            data=card_html,
            file_name="armillary-card.html",
            mime="text/html",
            icon=":material/download:",
        )


def _render_time_grouped_tables(rows: list[OverviewRow]) -> None:
    """Show projects in time groups: last month, last year, older.

    Only non-empty groups are rendered. Each gets a subheader + table.
    Uses the pure ``group_by_time`` helper so both prod and tests share
    the same bucketing logic.
    """
    groups = group_by_time(rows)
    this_month = groups["last_month"]
    this_year = groups["last_year"]
    older = groups["older"]

    if this_month:
        st.caption(f"Last month \u2014 {len(this_month)} projects")
        _render_table(this_month)
    if this_year:
        with st.expander(
            f"Last year \u2014 {len(this_year)} projects",
            expanded=False,
        ):
            _render_table(this_year)
    if older:
        with st.expander(
            f"Older \u2014 {len(older)} projects",
            expanded=False,
        ):
            _render_table(older)
    if not rows:
        st.warning("No projects in cache.")


def _render_table(rows: list[OverviewRow]) -> None:
    """Compact dataframe with multi-select + action bar."""
    display = []
    for r in rows:
        emoji = _STATUS_EMOJI.get(r.status_raw, "·")

        # Summary prose
        parts = []
        if r.commits is not None:
            parts.append(f"{r.commits} commits")
        if r.branch and r.branch != "—":
            parts.append(r.branch)
        summary = ", ".join(parts) if parts else "—"

        # Relative time
        if r.last_modified:
            delta = datetime.now() - r.last_modified
            days = delta.days
            if days == 0:
                last = "today"
            elif days == 1:
                last = "1d ago"
            elif days < 30:
                last = f"{days}d ago"
            elif days < 365:
                last = f"{days // 30}mo ago"
            else:
                last = f"{days // 365}y ago"
        else:
            last = "—"

        display.append(
            {
                "Name": f"{emoji} {r.name}",
                "Summary": summary,
                "Hours": r.work_hours or 0,
                "Last": last,
            }
        )

    event = st.dataframe(
        display,
        height=400,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        column_config={
            "Name": st.column_config.TextColumn("Name", pinned=True),
            "Summary": st.column_config.TextColumn("Summary"),
            "Hours": st.column_config.ProgressColumn(
                "Hours",
                min_value=0,
                max_value=max((r.work_hours or 0 for r in rows), default=100),
                format="%.0f",
                help="Estimated work hours (commit gap < 4h)",
            ),
            "Last": st.column_config.TextColumn("Last", width="small"),
        },
    )

    selection = getattr(event, "selection", None)
    selected_indices = getattr(selection, "rows", []) if selection else []

    if len(selected_indices) == 1:
        # Single click → navigate to detail
        idx = selected_indices[0]
        if idx < len(rows):
            st.query_params["project"] = rows[idx].path
            st.rerun()
    elif len(selected_indices) > 1:
        # Multi-select → bulk actions
        col_info, col_action = st.columns([3, 1])
        with col_info:
            st.caption(f"{len(selected_indices)} projects selected")
        with col_action:
            if st.button(
                "Archive selected",
                key="bulk_archive",
                icon=":material/archive:",
                type="secondary",
            ):
                from armillary.models import Status
                from armillary.status_override import set_override

                for i in selected_indices:
                    if i < len(rows):
                        set_override(rows[i].path, Status.ARCHIVED)
                st.toast(f"Archived {len(selected_indices)} projects")
                st.rerun()

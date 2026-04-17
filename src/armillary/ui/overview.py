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


def _render_overview() -> None:
    _render_header()

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

    if not dormant_explore:
        _render_next_suggestions()

    # Zombie alert — ACTIVE projects going stale (M3)
    if not dormant_explore:
        _render_zombie_alert_dashboard(rows)

    # "Dying projects" hook — below next, excludes already-suggested projects
    if not dormant_explore:
        from armillary.next_service import get_suggestions

        suggested_paths = {str(s.project.path) for s in get_suggestions()}
        _render_dying_metric(rows, exclude_paths=suggested_paths)

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

    # Search bar below table
    _render_search_section([r for r in rows if r.status_raw != "ARCHIVED"], cfg)

    # Weekly pulse (ADR 0018) — collapsible
    _render_pulse_section()

    # Activity heatmap (ADR 0020) — collapsible at bottom
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
    "forgotten_gold": "💀",
    "archive_candidate": "📦",
}
_CATEGORY_LABELS = {
    "momentum": "Momentum",
    "zombie": "Zombie — kill or ship?",
    "forgotten_gold": "Forgotten gold",
    "archive_candidate": "Archive this?",
}


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

    st.subheader(
        "What should you work on today?",
        anchor=False,
    )

    # Yesterday's activity — retention hook
    _render_yesterday_line(suggestions)

    for s in suggestions:
        icon = _CATEGORY_ICONS.get(s.category, "\u2022")
        label = _CATEGORY_LABELS.get(s.category, s.category)
        path_str = _shorten_home(s.project.path)

        purpose = get_purpose(str(s.project.path))
        md = s.project.metadata
        oneliner = ""
        if purpose:
            oneliner = f"*{purpose}*  \n"
        elif md and md.readme_excerpt:
            excerpt = md.readme_excerpt
            dot = excerpt.find(". ")
            short = excerpt[: dot + 1] if 0 < dot < 80 else excerpt[:80]
            oneliner = f"*{short}*  \n"

        # Sparkline inline
        spark = ""
        if md and md.monthly_commits and any(c > 0 for c in md.monthly_commits):
            chars = "".join(
                _spark_char(c, md.monthly_commits) for c in md.monthly_commits
            )
            spark = f"  \n`{chars}` (6mo)"

        col_info, col_open, col_skip = st.columns([4, 1, 1])
        with col_info:
            st.markdown(
                f"{icon} **{s.project.name}** \u2014 {label}  \n"
                f"{oneliner}"
                f"{s.reason}{spark}"
            )
            st.caption(f"`{path_str}`")
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
                icon=":material/inventory_2:",
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
        col_msg, col_btn = st.columns([4, 1])
        with col_msg:
            st.warning(
                f"**{len(dormant)} forgotten projects** — {total_hours:.0f}h invested",
                icon=":material/inventory_2:",
            )
        with col_btn:
            if st.button(
                "Explore",
                icon=":material/explore:",
                key="explore_dormant",
                type="primary",
            ):
                st.session_state["_dormant_explore"] = True
                st.rerun()


def _render_empty_cache_state(cfg: Config | None) -> None:
    """Friendly first-launch screen with scan button."""
    st.subheader("Cache is empty", anchor=False)
    st.write(
        "armillary needs to walk the filesystem at least once to discover "
        "your projects. Click the button below or use `armillary scan`."
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


_SPARK_CHARS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def _spark_char(value: int, all_values: list[int]) -> str:
    """Single sparkline character for a value within a series."""
    peak = max(all_values) or 1
    return _SPARK_CHARS[min(int(value / peak * 7), 7)]


def _render_zombie_alert_dashboard(rows: list[OverviewRow]) -> None:
    """Zombie alert in dashboard — mirrors CLI _print_zombie_alert."""
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
    if zombies:
        names = ", ".join(r.name for r in zombies[:3])
        more = f" +{len(zombies) - 3}" if len(zombies) > 3 else ""
        st.warning(
            f"**{len(zombies)} zombie"
            f"{'s' if len(zombies) > 1 else ''}: "
            f"{names}{more}** \u2014 no commit in 14+ days",
            icon=":material/warning:",
        )


def _render_dying_metric(
    rows: list[OverviewRow],
    *,
    exclude_paths: set[str] | None = None,
) -> None:
    """'Uncommitted work at risk' — panel consensus 3/3."""
    at_risk = find_at_risk_projects(rows, exclude_paths=exclude_paths)

    if not at_risk:
        return

    at_risk.sort(key=lambda r: r.work_hours or 0, reverse=True)
    total = len(at_risk)
    total_hours = sum(r.work_hours or 0 for r in at_risk)

    st.warning(
        f"**{total} project{'s have' if total > 1 else ' has'} "
        f"uncommitted work** \u2014 {total_hours:.0f}h invested, "
        f"commit or archive",
        icon=":material/priority_high:",
    )

    with st.expander(
        f"Show {total} project{'s' if total > 1 else ''}",
        expanded=False,
    ):
        for r in at_risk:
            hours = f"{r.work_hours:.0f}h" if r.work_hours else "0h"
            dirty_label = f"{r.dirty} file{'s' if r.dirty > 1 else ''}"
            col_info, col_act = st.columns([4, 1])
            with col_info:
                st.markdown(
                    f"**{r.name}** \u2014 {dirty_label} uncommitted \u00b7 {hours}"
                )
            with col_act:
                if st.button(
                    "Open",
                    key=f"dying_{r.path}",
                    icon=":material/open_in_new:",
                ):
                    st.query_params["project"] = r.path
                    st.rerun()


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

    with st.expander(
        "Weekly pulse",
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
        from armillary.pulse_service import load_history, take_snapshot

        # Take snapshot on view (idempotent per week)
        take_snapshot()
        history = load_history()
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
            "Download card",
            data=card_html,
            file_name="armillary-card.html",
            mime="text/html",
            icon=":material/download:",
        )


def _render_time_grouped_tables(rows: list[OverviewRow]) -> None:
    """Show projects in time groups: last month, last year, older.

    Only non-empty groups are rendered. Each gets a subheader + table.
    """
    from datetime import datetime, timedelta

    now = datetime.now()
    month_ago = now - timedelta(days=30)
    year_ago = now - timedelta(days=365)

    this_month = [r for r in rows if r.last_modified >= month_ago]
    this_year = [r for r in rows if month_ago > r.last_modified >= year_ago]
    older = [r for r in rows if r.last_modified < year_ago]

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

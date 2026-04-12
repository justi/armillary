"""Overview page — table of all cached projects with filters and search."""

from __future__ import annotations

import streamlit as st

from armillary import __version__
from armillary import exporter as exporter_mod
from armillary.cache import Cache, default_db_path
from armillary.config import Config, default_config_path
from armillary.ui.actions import run_scan_with_feedback
from armillary.ui.helpers import (
    OverviewRow,
    _load_overview_rows,
    _safe_load_config,
    _shorten_home,
)
from armillary.ui.search import _render_search_section
from armillary.ui.sidebar import _render_sidebar


def _render_overview() -> None:
    title_col, export_col = st.columns([5, 2])
    with title_col:
        st.title(":material/explore: armillary")
    with export_col:
        _render_export_for_ai_button()
    _render_header_caption()

    cfg = _safe_load_config()
    rows = _load_overview_rows()

    # Render the sidebar BEFORE branching on empty cache so the user can
    # always reach the Settings / Reload / Scan-now affordances, even on
    # a brand-new install with zero indexed projects. The sidebar
    # gracefully handles empty rows by skipping the filter widgets.
    filters = _render_sidebar(rows, cfg)
    name_filter = filters.pop("name_substring", "")

    if not rows:
        _render_empty_cache_state(cfg)
        return

    # Top-level search bar — runs ripgrep across cached projects on demand.
    _render_search_section(rows, cfg)

    filtered = _apply_filters(rows, filters=filters, search=name_filter)
    _render_summary_metrics(filtered, total=len(rows))

    if not filtered:
        st.warning("No projects match the current filters.")
        return

    _render_table(filtered)


def _render_empty_cache_state(cfg: Config | None) -> None:
    """Show a friendly first-launch screen with a real "Scan now" button.

    Replaces the old text hint that told the user to "go to the terminal" —
    that violated the rule "what you can't click in the UI doesn't exist".
    """
    st.subheader("Cache is empty", anchor=False)
    st.write(
        "armillary needs to walk the filesystem at least once to discover "
        "your projects. Click the button below to run a scan against the "
        "umbrellas in your config."
    )

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
                "No umbrellas configured. Run `armillary config --init` "
                "from your terminal first."
            )


def _render_header_caption() -> None:
    """Sub-title line with version, last scan time, and a config link."""
    parts = [f"v{__version__}"]

    # Last scan time = max(last_scanned_at) across all rows. Cheap query.
    try:
        with Cache() as cache:
            last_scanned_ts = cache.last_scan_time()
    except Exception:  # noqa: BLE001 — never let a header crash the page
        last_scanned_ts = None

    if last_scanned_ts:
        from datetime import datetime as _dt

        when = _dt.fromtimestamp(last_scanned_ts).strftime("%Y-%m-%d %H:%M")
        parts.append(f"last scan: {when}")

    parts.append(f"config: `{_shorten_home(default_config_path())}`")
    parts.append(f"cache: `{_shorten_home(default_db_path())}`")

    st.caption(" · ".join(parts))


def _render_export_for_ai_button() -> None:
    """Download-current-projects-as-markdown button for AI tools."""
    try:
        with Cache() as cache:
            projects = cache.list_projects()
    except Exception:  # noqa: BLE001 — never let the header crash the page
        projects = []

    markdown = exporter_mod.render_repos_index(projects)
    st.download_button(
        label="Download for AI",
        icon=":material/download:",
        data=markdown,
        file_name="repos-index.md",
        mime="text/markdown",
        help=(
            "Downloads a markdown snapshot of the current cache. "
            "For Claude Code auto-load, use Settings \u2192 Integrations."
        ),
        width="stretch",
        disabled=not projects,
    )
    st.caption("Claude Code auto-load: Settings \u2192 Integrations")


def _apply_filters(
    rows: list[OverviewRow],
    *,
    filters: dict[str, list[str]],
    search: str,
) -> list[OverviewRow]:
    out = rows
    if filters["status"]:
        out = [r for r in out if r.status_raw in filters["status"]]
    if filters["type"]:
        out = [r for r in out if r.type in filters["type"]]
    if filters["umbrella"]:
        out = [r for r in out if r.umbrella in filters["umbrella"]]
    if search:
        needle = search.lower()
        out = [r for r in out if needle in r.name.lower()]
    return out


def _render_summary_metrics(rows: list[OverviewRow], *, total: int) -> None:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.status_raw] = counts.get(r.status_raw, 0) + 1
    showing = len(rows)
    with st.container(horizontal=True):
        if showing < total:
            st.metric("Showing", f"{showing} of {total}", border=True)
        else:
            st.metric("Total", total, border=True)
        st.metric("Active", counts.get("ACTIVE", 0), border=True)
        st.metric("Paused", counts.get("PAUSED", 0), border=True)
        st.metric("Dormant", counts.get("DORMANT", 0), border=True)
        st.metric(
            "Ideas",
            counts.get("IDEA", 0) + counts.get("IN_PROGRESS", 0),
            border=True,
        )


def _render_table(rows: list[OverviewRow]) -> None:
    display = [r.display_dict() for r in rows]
    event = st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Status": st.column_config.TextColumn(
                "Status",
                width="small",
            ),
            "Name": st.column_config.TextColumn("Name", pinned=True),
            "Branch": st.column_config.TextColumn(
                "Branch",
                width="small",
            ),
            "Dirty": st.column_config.NumberColumn(
                "Dirty",
                format="%d",
                width="small",
                help="Unstaged + staged + untracked files",
            ),
            "Commits": st.column_config.ProgressColumn(
                "Commits",
                min_value=0,
                max_value=1000,
                format="%d",
                help="Total commits across all branches",
            ),
            "Work h": st.column_config.ProgressColumn(
                "Work h",
                min_value=0,
                max_value=500,
                format="%.0f",
                help=(
                    "Estimated active development hours "
                    "(sum of inter-commit gaps < 4 h)"
                ),
            ),
            "Last modified": st.column_config.DatetimeColumn(
                "Last modified",
                format="DD MMM YYYY",
            ),
            "Type": None,  # hide — almost all are "git"
        },
    )

    selection = getattr(event, "selection", None)
    selected_rows = getattr(selection, "rows", []) if selection else []
    if selected_rows:
        idx = selected_rows[0]
        st.query_params["project"] = rows[idx].path
        st.toast("Opening project\u2026")
        st.rerun()

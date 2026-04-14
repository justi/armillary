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

    filters = _render_sidebar(rows, cfg)

    if not rows:
        _render_empty_cache_state(cfg)
        return

    # Check if dormant explore mode is active
    dormant_explore = st.session_state.get("_dormant_explore", False)

    if not dormant_explore:
        _render_next_suggestions()

    _render_dormant_banner(rows, exploring=dormant_explore)

    # Search bar
    _render_search_section(rows, cfg)

    # Apply filters
    filtered = _apply_filters(rows, filters=filters)

    if dormant_explore:
        filtered = [r for r in filtered if r.status_raw == "DORMANT"]
        filtered.sort(key=lambda r: r.work_hours or 0, reverse=True)

    # Subtitle
    if dormant_explore:
        total_hours = sum(r.work_hours or 0 for r in filtered)
        st.caption(f"{len(filtered)} dormant projects · {total_hours:.0f}h total")
    else:
        active_count = sum(1 for r in filtered if r.status_raw == "ACTIVE")
        st.caption(f"{active_count} active of {len(filtered)} projects shown")

    if not filtered:
        st.warning("No projects match the current filters.")
        return

    _render_table(filtered)


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


_CATEGORY_ICONS = {"momentum": "🔥", "zombie": "⚠️", "forgotten_gold": "💀"}
_CATEGORY_LABELS = {
    "momentum": "Momentum",
    "zombie": "Zombie — kill or ship?",
    "forgotten_gold": "Forgotten gold",
}


def _render_next_suggestions() -> None:
    """Hero element: armillary next suggestions."""
    from armillary.next_service import get_suggestions

    suggestions = get_suggestions()
    if not suggestions:
        return

    with st.expander(
        "🧭 What should you work on today?",
        expanded=True,
        icon=":material/tips_and_updates:",
    ):
        for s in suggestions:
            icon = _CATEGORY_ICONS.get(s.category, "•")
            label = _CATEGORY_LABELS.get(s.category, s.category)
            path_str = _shorten_home(s.project.path)
            col_info, col_action = st.columns([4, 1])
            with col_info:
                st.markdown(
                    f"{icon} **{s.project.name}** — {label}  \n"
                    f"{s.reason}  \n"
                    f"`{path_str}`"
                )
            with col_action:
                if st.button(
                    "Open",
                    key=f"next_open_{s.project.name}",
                    icon=":material/open_in_new:",
                ):
                    st.query_params["view"] = "detail"
                    st.query_params["project"] = str(s.project.path)
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


def _apply_filters(
    rows: list[OverviewRow],
    *,
    filters: dict[str, list[str] | str],
) -> list[OverviewRow]:
    out = rows
    if filters["status"]:
        out = [r for r in out if r.status_raw in filters["status"]]
    return out


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
                max_value=500,
                format="%.0f",
                help="Estimated work hours (commit gap < 4h)",
            ),
            "Last": st.column_config.TextColumn("Last", width="small"),
        },
    )

    # Selection handling
    selection = getattr(event, "selection", None)
    selected_indices = getattr(selection, "rows", []) if selection else []

    if len(selected_indices) == 1:
        # Single row click → navigate to detail (same tab)
        idx = selected_indices[0]
        if idx < len(rows):
            st.query_params["view"] = "detail"
            st.query_params["project"] = rows[idx].path
            st.rerun()
    elif len(selected_indices) > 1:
        # Multi-select → bulk actions
        _render_bulk_action_bar(rows, selected_indices)


def _render_bulk_action_bar(
    rows: list[OverviewRow], selected_indices: list[int]
) -> None:
    """Bulk action bar for multi-selected projects."""
    from armillary.exclude_service import exclude_project

    selected_paths = [rows[i].path for i in selected_indices if i < len(rows)]
    col_info, col_exclude = st.columns([3, 1])
    with col_info:
        st.caption(f"**{len(selected_paths)}** project(s) selected")
    with col_exclude:
        if st.button(
            "Exclude selected",
            icon=":material/visibility_off:",
            key="action_bulk_exclude",
        ):
            for p in selected_paths:
                exclude_project(p)
            st.rerun()

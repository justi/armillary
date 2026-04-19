"""Overview page — thin orchestrator for the sibling view modules.

The overview page is composed of several independent sections; each
lives in its own module to keep every file under the architecture
target (ADR 0001 §3). This file wires them together and handles the
parts that don't belong to any specific section (header, empty-cache
state, top-level routing, query-param handling).

Pure helpers (``apply_status_filter``, ``find_at_risk_projects``,
``find_today_activity``, ``group_by_time``, ``build_hero_headline``)
are re-exported here for stable imports from tests and callers.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from armillary.cache import Cache
from armillary.config import Config
from armillary.exclude_service import filter_excluded
from armillary.ui.helpers import (
    OverviewRow,
    _load_overview_rows,
    _safe_load_config,
    _shorten_home,
)
from armillary.ui.overview_portfolio import (
    _render_activity_heatmap,
    _render_pulse_section,
)
from armillary.ui.overview_status import (
    _render_dormant_banner,
    _render_status_strip,
    apply_status_filter,
    find_at_risk_projects,
)
from armillary.ui.overview_suggestions import (
    _render_next_suggestions,
    _render_transitions,
    build_hero_headline,
)
from armillary.ui.overview_table import (
    _render_table,
    _render_time_grouped_tables,
    group_by_time,
)
from armillary.ui.overview_today import (
    _render_today_activity,
    find_today_activity,
)
from armillary.ui.search import _render_search_section
from armillary.ui.sidebar import _render_sidebar

__all__ = [
    # Pure helpers kept re-exported for tests / external imports.
    "apply_status_filter",
    "find_at_risk_projects",
    "find_today_activity",
    "group_by_time",
    "build_hero_headline",
    # Render entry point.
    "_render_overview",
]


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

    filters = _render_sidebar(rows, cfg)

    if not rows:
        _render_empty_cache_state(cfg)
        return

    dormant_explore = st.session_state.get("_dormant_explore", False)

    if not dormant_explore:
        _render_transitions()
        _render_next_suggestions()

        from armillary.next_service import get_suggestions

        suggested_paths = {str(s.project.path) for s in get_suggestions()}
        _render_status_strip(rows, exclude_paths=suggested_paths)

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
        filtered = apply_status_filter(rows, filters["status"])
        st.caption(f"{len(filtered)} projects")
        if filtered:
            _render_table(filtered)
        else:
            st.warning("No projects match the current filters.")
    else:
        all_visible = [r for r in rows if r.status_raw != "ARCHIVED"]
        _render_time_grouped_tables(all_visible)

    # Portfolio tab — pulse + heatmap + search.
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


def _apply_filters(
    rows: list[OverviewRow],
    *,
    filters: dict[str, list[str] | str],
) -> list[OverviewRow]:
    return apply_status_filter(rows, filters["status"])

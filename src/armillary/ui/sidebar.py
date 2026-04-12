"""Sidebar with filters, reload, scan, and settings navigation.

Two variants:
- ``_render_sidebar`` — full version with filter widgets for the overview page
- ``_render_nav_sidebar`` — minimal version for detail/settings pages with
  just the navigation buttons so the user can always get back, reload, or scan
"""

from __future__ import annotations

import streamlit as st

from armillary.config import Config
from armillary.ui.actions import (
    go_to_overview,
    go_to_settings,
    refresh_cache,
    run_scan_with_feedback,
)
from armillary.ui.helpers import OverviewRow, _safe_load_config


def _render_sidebar(
    rows: list[OverviewRow], cfg: Config | None
) -> dict[str, list[str] | str]:
    """Full sidebar for the overview page — filters + actions."""
    status_pick: list[str] = []
    type_pick: list[str] = []
    umbrella_pick: list[str] = []
    name_substring = ""

    with st.sidebar:
        if rows:
            st.header("Filters")
            statuses = sorted({r.status_raw for r in rows if r.status_raw})
            types = sorted({r.type for r in rows})
            umbrellas = sorted({r.umbrella for r in rows})

            status_pick = st.multiselect("Status", statuses)
            type_pick = st.multiselect("Type", types)
            umbrella_pick = st.multiselect("Umbrella", umbrellas)
            name_substring = st.text_input(
                "Name contains",
                placeholder="quick filter…",
            )

            st.divider()
            st.caption(f"{len(rows)} projects in cache")
        else:
            st.header("armillary")
            st.caption("Cache is empty — scan filesystem to populate.")

        _render_sidebar_actions(cfg)

    return {
        "status": status_pick,
        "type": type_pick,
        "umbrella": umbrella_pick,
        "name_substring": name_substring,
    }


def _render_nav_sidebar() -> None:
    """Minimal navigation sidebar for detail and settings pages.

    Always visible so the user is never stranded without a way to get
    back to the overview, reload the cache, or trigger a scan.
    """
    cfg = _safe_load_config()
    with st.sidebar:
        st.header("armillary")
        if st.button(
            "🔭 Overview",
            use_container_width=True,
            key="sidebar_back_overview",
        ):
            go_to_overview()
        _render_sidebar_actions(cfg)


def _render_sidebar_actions(cfg: Config | None) -> None:
    """Shared action buttons rendered in every sidebar variant."""
    if st.button(
        "🔄 Reload from cache",
        use_container_width=True,
        key="sidebar_reload",
    ):
        refresh_cache()

    scan_disabled = cfg is None or not cfg.umbrellas
    scan_help = None
    if scan_disabled:
        scan_help = "No umbrellas in config. Run `armillary config --init`."
    if st.button(
        "🔁 Scan filesystem now",
        use_container_width=True,
        disabled=scan_disabled,
        help=scan_help,
        key="sidebar_scan",
    ):
        run_scan_with_feedback(cfg)

    st.divider()
    if st.button(
        "⚙️ Settings",
        use_container_width=True,
        key="sidebar_settings",
    ):
        go_to_settings()

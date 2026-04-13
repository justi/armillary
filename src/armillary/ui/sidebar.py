"""Sidebar — navigation + status filter + scan.

Two variants:
- ``_render_sidebar`` — overview page: navigation + status filter + scan
- ``_render_nav_sidebar`` — detail/settings pages: navigation + scan only
"""

from __future__ import annotations

import streamlit as st

from armillary.config import Config
from armillary.ui.actions import (
    go_to_overview,
    go_to_settings,
    run_scan_with_feedback,
)
from armillary.ui.helpers import OverviewRow, _safe_load_config


def _render_sidebar(
    rows: list[OverviewRow], cfg: Config | None
) -> dict[str, list[str] | str]:
    """Sidebar for overview: navigation + status filter + scan."""
    status_pick: list[str] = []

    with st.sidebar:
        st.header(":material/explore: armillary")

        # Navigation
        if st.button(
            "Overview",
            icon=":material/explore:",
            width="stretch",
            key="sidebar_overview",
        ):
            go_to_overview()
        if st.button(
            "Settings",
            icon=":material/settings:",
            width="stretch",
            key="sidebar_settings",
        ):
            go_to_settings()

        # Status filter
        if rows:
            st.markdown("---")
            statuses = sorted({r.status_raw for r in rows if r.status_raw})
            status_pick = st.pills(
                "Status",
                statuses,
                selection_mode="multi",
                default=None,
            )

        # Scan
        st.markdown("---")
        _render_scan_button(cfg)

    return {
        "status": status_pick,
        "type": [],
        "umbrella": [],
        "name_substring": "",
    }


def _render_nav_sidebar() -> None:
    """Minimal navigation sidebar for detail and settings pages."""
    cfg = _safe_load_config()
    with st.sidebar:
        st.header(":material/explore: armillary")
        if st.button(
            "Overview",
            icon=":material/explore:",
            width="stretch",
            key="sidebar_back_overview",
        ):
            go_to_overview()
        if st.button(
            "Settings",
            icon=":material/settings:",
            width="stretch",
            key="sidebar_settings",
        ):
            go_to_settings()
        st.markdown("---")
        _render_scan_button(cfg)


def _render_scan_button(cfg: Config | None) -> None:
    """Scan filesystem button — shared across sidebar variants."""
    scan_disabled = cfg is None or not cfg.umbrellas
    scan_help = None
    if scan_disabled:
        scan_help = "No umbrellas in config. Run `armillary config --init`."
    if st.button(
        "Scan now",
        icon=":material/sync:",
        width="stretch",
        disabled=scan_disabled,
        help=scan_help,
        key="sidebar_scan",
    ):
        run_scan_with_feedback(cfg)

"""Sidebar with filters, reload, scan, and settings navigation."""

from __future__ import annotations

import streamlit as st

from armillary.config import Config
from armillary.ui.actions import go_to_settings, refresh_cache, run_scan_with_feedback
from armillary.ui.helpers import OverviewRow


def _render_sidebar(
    rows: list[OverviewRow], cfg: Config | None
) -> dict[str, list[str] | str]:
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

        # Two distinct refresh paths:
        #   "Reload from cache" — cheap, just rereads SQLite (clears the
        #   60-second TTL on st.cache_data). Use after `armillary scan`
        #   from a terminal.
        #   "Scan filesystem now" — expensive, walks the umbrellas, runs
        #   metadata extraction, computes status, persists to cache.
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

    return {
        "status": status_pick,
        "type": type_pick,
        "umbrella": umbrella_pick,
        "name_substring": name_substring,
    }

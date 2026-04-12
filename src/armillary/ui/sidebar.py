"""Sidebar with filters, reload, scan, and settings navigation."""

from __future__ import annotations

from typing import Any

import streamlit as st

from armillary.config import Config
from armillary.ui.helpers import (
    _load_overview_rows,
    _load_project,
    _run_dashboard_scan,
)


def _render_sidebar(rows: list[dict[str, Any]], cfg: Config | None) -> dict[str, Any]:
    status_pick: list[str] = []
    type_pick: list[str] = []
    umbrella_pick: list[str] = []
    name_substring = ""

    with st.sidebar:
        # Filter widgets are only useful when there is something to filter.
        # On the empty-cache state we still render the sidebar (so the user
        # can reach Settings / Reload / Scan-now), just without the filter
        # multiselects pointing at empty option lists.
        if rows:
            st.header("Filters")
            statuses = sorted({r["_status_raw"] for r in rows if r["_status_raw"]})
            types = sorted({r["Type"] for r in rows})
            umbrellas = sorted({r["Umbrella"] for r in rows})

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
            _load_overview_rows.clear()
            _load_project.clear()
            st.rerun()

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
            with st.spinner("Scanning…"):
                ok, message = _run_dashboard_scan(cfg)
            if ok:
                st.success(message)
                st.rerun()
            else:
                st.error(message)

        st.divider()
        if st.button(
            "⚙️ Settings",
            use_container_width=True,
            key="sidebar_settings",
        ):
            st.query_params["page"] = "settings"
            st.rerun()

    return {
        "status": status_pick,
        "type": type_pick,
        "umbrella": umbrella_pick,
        "name_substring": name_substring,
    }

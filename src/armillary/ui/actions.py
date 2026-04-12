"""Shared UI action sequences — deduplicated from sidebar, overview,
settings, and detail modules."""

from __future__ import annotations

import contextlib

import streamlit as st

from armillary.config import Config, write_config
from armillary.ui.helpers import (
    _load_overview_rows,
    _load_project,
    _run_dashboard_scan,
)


def refresh_cache() -> None:
    """Clear st.cache_data caches and rerun."""
    _load_overview_rows.clear()
    _load_project.clear()
    st.rerun()


def go_to_overview() -> None:
    """Navigate to overview by clearing query params."""
    with contextlib.suppress(KeyError):
        del st.query_params["page"]
    st.query_params.pop("project", None)
    st.rerun()


def go_to_settings() -> None:
    """Navigate to settings page."""
    st.query_params["page"] = "settings"
    st.rerun()


def run_scan_with_feedback(cfg: Config | None) -> None:
    """Run scan with spinner, show result, rerun on success."""
    with st.spinner("Scanning\u2026"):
        ok, message = _run_dashboard_scan(cfg)
    if ok:
        st.success(message)
        st.rerun()
    else:
        st.error(message)


def save_config_and_refresh(cfg: Config) -> None:
    """Write config, clear caches, show success, rerun."""
    try:
        write_config(cfg)
    except OSError as exc:
        st.error(f"Could not write config: {exc}")
        return
    _load_overview_rows.clear()
    _load_project.clear()
    st.success("Saved.")
    st.rerun()

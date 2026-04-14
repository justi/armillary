"""Settings page shell for the in-UI config editor."""

from __future__ import annotations

import streamlit as st

from armillary.config import (
    ConfigError,
    default_config_path,
    load_config,
)
from armillary.ui.helpers import _shorten_home
from armillary.ui.settings_editors import (
    render_settings_launchers,
    render_settings_umbrellas,
)
from armillary.ui.settings_tabs import render_settings_integrations

_SETTINGS_TOAST_KEY = "_settings_toast"


def _render_settings_page() -> None:
    """In-UI editor for the YAML config — umbrellas, launchers, integrations.

    Replaces the "edit YAML by hand" workflow per the user-stated rule
    "what you can't click in the UI doesn't exist". Three tabs, each
    with its own form + Save button.

    Loading the page itself is read-only — the only filesystem writes
    happen on explicit "Save" button clicks.
    """
    from armillary.ui.sidebar import _render_nav_sidebar

    _render_nav_sidebar()

    # P1.2: Show toast feedback from the previous save/add/remove action.
    toast_msg = st.session_state.pop(_SETTINGS_TOAST_KEY, None)
    if toast_msg:
        st.toast(toast_msg)

    st.title(":material/settings: Settings")
    st.caption(f"Editing `{_shorten_home(default_config_path())}`")

    try:
        cfg = load_config()
    except ConfigError as exc:
        msg = str(exc)
        # Surface just the first line (usually the human-readable summary)
        # and hide the full traceback behind an expander.
        short = msg.splitlines()[0] if msg else "Unknown error"
        st.error(f"Config could not be loaded: {short}")
        if len(msg.splitlines()) > 1:
            with st.expander("Full error details"):
                st.code(msg, language="text")
        st.info(
            "Fix the YAML by hand (`armillary config` from a terminal), "
            "then click Reload below."
        )
        if st.button("Reload config", icon=":material/refresh:"):
            st.rerun()
        return

    tabs = st.tabs(
        [
            ":material/folder_open: Umbrellas",
            ":material/launch: Launchers",
            ":material/visibility_off: Exclusions",
            ":material/extension: Integrations",
        ]
    )
    with tabs[0]:
        render_settings_umbrellas(cfg)
    with tabs[1]:
        render_settings_launchers(cfg)
    with tabs[2]:
        _render_settings_exclusions()
    with tabs[3]:
        render_settings_integrations()


def _render_settings_exclusions() -> None:
    """Two-column view: all projects (left) ↔ excluded projects (right)."""
    from armillary.cache import Cache
    from armillary.exclude_service import (
        exclude_project,
        include_project,
        load_excluded,
    )

    st.subheader("Project exclusions")
    st.caption(
        "Excluded projects are hidden from overview, search, next, "
        "and MCP tools. They remain in the cache and can be restored."
    )

    with Cache() as cache:
        all_projects = cache.list_projects()

    excluded_paths = load_excluded()
    included = [p for p in all_projects if str(p.path) not in excluded_paths]
    excluded = [p for p in all_projects if str(p.path) in excluded_paths]

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown(f"**Included** ({len(included)})")
        incl_filter = st.text_input(
            "Filter included",
            placeholder="Type to filter…",
            key="excl_filter_included",
            label_visibility="collapsed",
        )
        filtered_incl = (
            [p for p in included if incl_filter.lower() in p.name.lower()]
            if incl_filter
            else included
        )
        with st.container(height=400):
            for p in filtered_incl:
                col_name, col_btn = st.columns([4, 1])
                with col_name:
                    st.caption(p.name)
                with col_btn:
                    if st.button(
                        "→",
                        key=f"excl_{p.path}",
                        help=f"Exclude {p.name}",
                    ):
                        exclude_project(str(p.path))
                        st.rerun()

    with col_right:
        st.markdown(f"**Excluded** ({len(excluded)})")
        excl_filter = st.text_input(
            "Filter excluded",
            placeholder="Type to filter…",
            key="excl_filter_excluded",
            label_visibility="collapsed",
        )
        filtered_excl = (
            [p for p in excluded if excl_filter.lower() in p.name.lower()]
            if excl_filter
            else excluded
        )
        with st.container(height=400):
            if not filtered_excl:
                st.caption("No excluded projects.")
            for p in filtered_excl:
                col_name, col_btn = st.columns([4, 1])
                with col_name:
                    st.caption(p.name)
                with col_btn:
                    if st.button(
                        "←",
                        key=f"incl_{p.path}",
                        help=f"Restore {p.name}",
                    ):
                        include_project(str(p.path))
                        st.rerun()

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
            ":material/extension: Integrations",
        ]
    )
    with tabs[0]:
        render_settings_umbrellas(cfg)
    with tabs[1]:
        render_settings_launchers(cfg)
    with tabs[2]:
        render_settings_integrations()

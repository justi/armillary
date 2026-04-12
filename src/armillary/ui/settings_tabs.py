"""Settings UI tab for Integrations.

Extracted from settings.py to keep modules under 400 lines.
This is a UI module (imports streamlit), not a service layer module.
"""

from __future__ import annotations

import streamlit as st

from armillary import exporter as exporter_mod

# ----- Integrations tab ----------------------------------------------------


def render_settings_integrations() -> None:
    st.subheader("Integrations")
    st.caption(
        "Connect armillary outputs to external tools. "
        "Downloads are one-off snapshots; integrations write to a stable path."
    )
    _render_claude_code_integration()


def _render_claude_code_integration() -> None:
    status = exporter_mod.get_claude_bridge_status()

    st.markdown("**Claude Code**")
    st.write(
        "The Claude bridge writes your current project index to "
        "`~/.claude/armillary/repos-index.md`. "
        "Optionally it also adds `@armillary/repos-index.md` to "
        "`~/.claude/CLAUDE.md`, so new Claude Code sessions load that index "
        "automatically."
    )

    with st.expander(
        "How this works",
        icon=":material/info:",
        expanded=not status.bridge_installed,
    ):
        st.markdown(
            "1. `Install / Update` writes a markdown snapshot from the current cache.\n"
            "2. If CLAUDE.md wiring is enabled, armillary adds the import line once.\n"
            "3. Re-run `Install / Update` after a new scan when you want to refresh "
            "the snapshot used by Claude Code."
        )
        st.caption(
            "This action only adds wiring. It does not remove an existing "
            "`@armillary/repos-index.md` import."
        )

    with st.container(horizontal=True):
        st.metric(
            "Bridge file",
            "Installed" if status.bridge_installed else "Not installed",
            border=True,
        )
        if status.claude_md_wired:
            wiring_status = "Active"
        elif status.claude_md_exists:
            wiring_status = "Not wired"
        else:
            wiring_status = "Missing"
        st.metric("CLAUDE.md wiring", wiring_status, border=True)

    st.caption(f"Bridge path: `{status.bridge_path}`")
    st.caption(f"CLAUDE.md: `{status.claude_md_path}`")

    wire_claude_md = st.checkbox(
        "Also add wiring to ~/.claude/CLAUDE.md if missing",
        value=status.claude_md_wired,
        key="claude_bridge_wire_claude_md",
        help=(
            "Adds `@armillary/repos-index.md` if it is not already present. "
            "Unchecking this does not remove existing wiring."
        ),
    )

    action_label = (
        "Update Claude bridge" if status.bridge_installed else "Install Claude bridge"
    )
    if st.button(
        action_label,
        key="claude_bridge_install",
        type="primary",
        width="stretch",
    ):
        try:
            bridge_path, written, appended = exporter_mod.install_claude_bridge(
                with_claude_md=wire_claude_md
            )
        except Exception as exc:  # noqa: BLE001 — surface install errors inline
            st.error(f"Could not install Claude bridge: {exc}")
            return

        refreshed = exporter_mod.get_claude_bridge_status()
        st.success(
            f"Wrote {written} project(s) to `{bridge_path}`."
            + (
                " Added the import line to CLAUDE.md."
                if wire_claude_md and appended
                else " CLAUDE.md was already wired."
                if wire_claude_md and refreshed.claude_md_wired
                else ""
            )
        )
        if written == 0:
            st.warning(
                "The bridge is installed, but the cache is empty. Run a scan and then "
                "use `Update Claude bridge` to refresh the snapshot."
            )

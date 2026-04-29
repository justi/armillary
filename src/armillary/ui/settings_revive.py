"""Revive settings tab — capability status, global hook install, bulk prompts.

Two responsibilities:

1. Show the user where revive is installed, whether it is compatible
   with the commands armillary calls, and which hook scope is currently
   active.
2. Offer two write actions: install the global hook (one-shot, with
   path disclosure and a strong warning) and bulk-generate suggest
   prompts for selected ACTIVE/STALLED projects (DORMANT projects are
   intentionally excluded — see panel review).

All filesystem work lives in ``armillary.revive_service``. This module
is only Streamlit rendering + button wiring.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from armillary.revive_service import (
    generate_suggest_prompts,
    install_hook_global,
    probe_capability,
    project_status,
)

_PROMPTS_DIR = Path.home() / ".armillary" / "revive-prompts"
_GLOBAL_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
_CONFIRM_KEY = "_revive_install_global_confirm"


def render_revive_settings(active_stalled_paths: list[Path]) -> None:
    """Render the Revive integrations tab."""
    st.subheader("Revive")
    st.caption(
        "context-revive injects a per-project brief on every "
        "UserPromptSubmit. armillary surfaces its state across the "
        "portfolio so you can see which projects already have a brief."
    )

    capability = probe_capability()
    _render_capability_panel(capability)

    if not capability.binary_available:
        return
    if not capability.compatible:
        return

    st.markdown("---")
    _render_global_hook_section()
    st.markdown("---")
    _render_bulk_prompts_section(active_stalled_paths)


def _render_capability_panel(capability: object) -> None:
    binary_label = (
        f"`{capability.binary_path}`" if capability.binary_available else "not found"
    )
    compat_label = "compatible" if capability.compatible else "incompatible / unknown"
    version_label = capability.version or "unknown"
    with st.container(horizontal=True):
        st.metric("Binary", binary_label, border=True)
        st.metric("Compatibility", compat_label, border=True)
        st.metric("Version", version_label, border=True)
    if not capability.binary_available:
        st.info(
            "Install context-revive (https://github.com/your-fork) and put "
            "the `revive` binary on your PATH to enable this section.",
            icon=":material/info:",
        )
    elif not capability.compatible:
        st.warning(
            "The `revive` binary on your PATH is missing one of: "
            "`show`, `install-hook`, `doctor`. Update it.",
            icon=":material/warning:",
        )


def _render_global_hook_section() -> None:
    project_status_self = project_status(Path.cwd())
    hook_scope = project_status_self.hook_scope

    st.markdown("**Global hook**")
    st.caption(
        f"Current scope for this project: `{hook_scope}`. "
        f"Installing globally writes to `{_GLOBAL_SETTINGS_PATH}` and "
        "fires `revive refresh` on every UserPromptSubmit in **every** "
        "Claude Code session — not just armillary."
    )

    confirmed = st.session_state.get(_CONFIRM_KEY, False)
    if confirmed:
        st.warning(
            "Click again to confirm. This modifies your global Claude Code settings.",
            icon=":material/warning:",
        )

    if st.button(
        "Install global hook",
        key="revive_install_global_btn",
        icon=":material/download:",
        type="primary",
        disabled=hook_scope in ("global", "both"),
    ):
        if not confirmed:
            st.session_state[_CONFIRM_KEY] = True
            st.rerun()
        st.session_state.pop(_CONFIRM_KEY, None)
        success, output = install_hook_global()
        if success:
            st.success("Global hook installed.")
        else:
            st.error(f"Install failed: {output}")
        if output:
            with st.expander("CLI output"):
                st.code(output, language="text")


def _render_bulk_prompts_section(active_stalled_paths: list[Path]) -> None:
    st.markdown("**Bulk: generate suggest prompts**")
    st.caption(
        "Select ACTIVE or STALLED projects to generate a `revive suggest` "
        "prompt for each. Prompts land in "
        f"`{_PROMPTS_DIR}` — not inside the project. "
        "DORMANT projects are excluded by design (would emit placeholder "
        "briefs in every future session)."
    )

    if not active_stalled_paths:
        st.info(
            "No ACTIVE or STALLED projects available. Run a scan first.",
            icon=":material/info:",
        )
        return

    options = {str(p): p for p in sorted(active_stalled_paths, key=lambda p: p.name)}
    selected = st.multiselect(
        "Projects",
        options=list(options.keys()),
        format_func=lambda key: f"{Path(key).name}  —  {key}",
        key="revive_bulk_selected",
        label_visibility="collapsed",
    )

    if st.button(
        "Generate suggest prompts",
        key="revive_bulk_generate_btn",
        icon=":material/note_add:",
        type="secondary",
        disabled=not selected,
    ):
        chosen = [options[key] for key in selected]
        written = generate_suggest_prompts(chosen, output_dir=_PROMPTS_DIR)
        if not written:
            st.error(
                "No prompts were written. Verify the selected projects "
                "are git repos and `revive suggest` works in them."
            )
            return
        st.success(f"Wrote {len(written)} prompt(s) to `{_PROMPTS_DIR}`.")
        with st.expander("Files written"):
            for path in written:
                st.markdown(f"- `{path}`")

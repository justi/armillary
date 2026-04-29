"""Revive integration section for the project detail page.

Surfaces context-revive state per project and lets the user move from
one state to the next via copy-paste prompts: scaffold the file with
`revive init`, then copy a `revive suggest` prompt into a fresh Claude
Code session in the project, then a `revive audit` prompt for the
gap-check pass.

We intentionally do NOT spawn headless Claude here. Running the prompt
in the user's own Claude Code session (in the target project's cwd)
gives them full visibility, no surprise cost, and matches revive's
documented "fresh agent session" requirement for the audit pass.

This module is a thin Streamlit renderer; subprocess + filesystem
work lives in ``armillary.revive_service``.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from armillary.revive_service import (
    ReviveError,
    copy_to_clipboard,
    generate_audit_prompt,
    generate_suggest_prompt,
    launch_claude_yolo,
    probe_capability,
    project_status,
    revive_show,
    run_revive_init,
)

_BRIEF_STATE_LABELS = {
    "configured": "✓ configured",
    "stub": "⚠ stub — finish `revive suggest`",
    "placeholder": "⚠ placeholder",
    "missing": "✗ not set up",
    "unknown": "? unknown",
}
_HOOK_SCOPE_LABELS = {
    "none": "no hook",
    "project": "project hook",
    "global": "global hook",
    "both": "project + global hook",
}


def render_revive_section(project_path: Path) -> None:
    """Render the Revive integration block for one project."""
    capability = probe_capability()
    st.markdown("---")
    st.subheader(":material/auto_stories: Revive", anchor=False)

    if not capability.binary_available:
        st.caption(
            "`revive` binary not found on PATH — install context-revive "
            "to enable per-session brief injection."
        )
        return
    if not capability.compatible:
        st.warning(
            f"`revive` at `{capability.binary_path}` is missing required "
            "subcommands. Update or reinstall context-revive.",
            icon=":material/warning:",
        )
        return

    status = project_status(project_path)
    label = _BRIEF_STATE_LABELS[status.brief_state]
    hook_label = _HOOK_SCOPE_LABELS[status.hook_scope]
    st.caption(f"{label} · {hook_label}")

    if status.purpose_line and status.brief_state in ("configured", "stub"):
        st.markdown(f"_{status.purpose_line}_")

    if status.brief_state == "stub":
        st.info(
            "PURPOSE is filled but INVARIANTS and GOTCHAS are empty. "
            "Copy the suggest prompt and paste it in a fresh Claude Code "
            "session to fill them in.",
            icon=":material/info:",
        )

    _render_actions(project_path, status.brief_state)


def _render_actions(project_path: Path, brief_state: str) -> None:
    with st.container(horizontal=True):
        if brief_state == "missing":
            _render_init_button(project_path)
        if brief_state in ("placeholder", "stub", "configured"):
            _render_copy_suggest_button(project_path, brief_state)
        if brief_state == "configured":
            _render_copy_audit_button(project_path)
        if brief_state != "missing":
            _render_launch_claude_button(project_path)
        if brief_state in ("configured", "stub"):
            _render_preview_button(project_path)


def _render_init_button(project_path: Path) -> None:
    button_key = f"revive_init_{project_path}"
    if st.button(
        "Scaffold (`revive init`)",
        key=button_key,
        icon=":material/note_add:",
        type="primary",
        help=(
            "Creates `.revive/static.md` with PURPOSE auto-extracted from "
            "README/manifest. No LLM call. After scaffolding, copy the "
            "suggest prompt to fill INVARIANTS and GOTCHAS."
        ),
    ):
        success, output = run_revive_init(project_path)
        # Toast survives the rerun below; inline st.success / st.error
        # would be wiped before the user can read them.
        if success:
            st.toast("Scaffolded `.revive/static.md`.", icon="✅")
        else:
            # `output` already names the action (e.g. "revive init failed:
            # ...") — surface it verbatim so we don't double-prefix.
            st.toast(output or "`revive init` failed.", icon="⚠️")
        st.rerun()


def _render_copy_suggest_button(project_path: Path, brief_state: str) -> None:
    button_key = f"revive_copy_suggest_{project_path}"
    if st.button(
        "Copy suggest prompt",
        key=button_key,
        icon=":material/content_copy:",
        type="primary" if brief_state in ("placeholder", "stub") else "secondary",
        help=(
            "Copies the `revive suggest` LLM prompt to your clipboard. "
            "Click Launch Claude (yolo) next, then paste in the new tab "
            "to fill INVARIANTS and GOTCHAS."
        ),
    ):
        _generate_and_copy(project_path, generate_suggest_prompt, "suggest")


def _render_copy_audit_button(project_path: Path) -> None:
    button_key = f"revive_copy_audit_{project_path}"
    if st.button(
        "Copy audit prompt",
        key=button_key,
        icon=":material/fact_check:",
        type="secondary",
        help=(
            "Copies the second-pass gap-audit prompt. Paste into a NEW "
            "Claude Code session — fresh context is required by design."
        ),
    ):
        _generate_and_copy(project_path, generate_audit_prompt, "audit")


def _generate_and_copy(project_path: Path, fetch, label: str) -> None:
    """Run a prompt-producing revive subcommand, then push to clipboard.

    Single-click UX: no intermediate render of the prompt, no second
    button. Toast on success/failure so the message survives Streamlit
    reruns.
    """
    try:
        prompt = fetch(project_path)
    except ReviveError as exc:
        st.toast(f"Could not generate {label} prompt: {exc}", icon="⚠️")
        return
    if copy_to_clipboard(prompt):
        st.toast(
            f"{label.capitalize()} prompt copied — paste in a Claude tab.",
            icon="📋",
        )
    else:
        st.toast(
            "`pbcopy` unavailable — re-run from a macOS terminal.",
            icon="⚠️",
        )


def _render_launch_claude_button(project_path: Path) -> None:
    button_key = f"revive_launch_{project_path}"
    if st.button(
        "Launch Claude (yolo)",
        key=button_key,
        icon=":material/rocket_launch:",
        type="secondary",
        help=(
            "Opens a new iTerm tab in this project running "
            "`claude --dangerously-skip-permissions`. Pair with Copy "
            "suggest / Copy audit. macOS only."
        ),
    ):
        success, message = launch_claude_yolo(project_path)
        if success:
            st.toast(message, icon="🚀")
        else:
            st.toast(f"Launch failed: {message}", icon="⚠️")


def _render_preview_button(project_path: Path) -> None:
    button_key = f"revive_preview_{project_path}"
    if st.button(
        "Preview brief",
        key=button_key,
        icon=":material/visibility:",
        type="secondary",
    ):
        try:
            output = revive_show(project_path)
        except ReviveError as exc:
            st.error(f"Could not run `revive show`: {exc}")
            return
        st.code(output, language="markdown")

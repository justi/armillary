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
    generate_audit_prompt,
    generate_suggest_prompt,
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
_SUGGEST_KEY_PREFIX = "_revive_suggest_prompt_"
_AUDIT_KEY_PREFIX = "_revive_audit_prompt_"


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
            "Copy the suggest prompt below into a fresh Claude Code "
            "session in this project to fill them in.",
            icon=":material/info:",
        )

    _render_actions(project_path, status.brief_state)
    _render_pending_prompts(project_path)


def _render_actions(project_path: Path, brief_state: str) -> None:
    with st.container(horizontal=True):
        if brief_state == "missing":
            _render_init_button(project_path)
        if brief_state in ("placeholder", "stub", "configured"):
            _render_copy_suggest_button(project_path, brief_state)
        if brief_state == "configured":
            _render_copy_audit_button(project_path)
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
        if success:
            st.success("Scaffolded `.revive/static.md`.")
        else:
            st.error(f"`revive init` failed: {output}")
        st.rerun()


def _render_copy_suggest_button(project_path: Path, brief_state: str) -> None:
    button_key = f"revive_copy_suggest_{project_path}"
    label = (
        "Refresh suggest prompt"
        if brief_state == "configured"
        else "Copy suggest prompt"
    )
    if st.button(
        label,
        key=button_key,
        icon=":material/content_copy:",
        type="primary" if brief_state in ("placeholder", "stub") else "secondary",
        help=(
            "Generates the LLM-ready prompt that fills INVARIANTS and "
            "GOTCHAS. Paste it into a fresh Claude Code session in this "
            "project — Claude reads the repo and edits `.revive/static.md` "
            "interactively, with your usual approval flow."
        ),
    ):
        try:
            prompt = generate_suggest_prompt(project_path)
        except ReviveError as exc:
            st.error(f"Could not generate suggest prompt: {exc}")
            return
        st.session_state[f"{_SUGGEST_KEY_PREFIX}{project_path}"] = prompt
        st.rerun()


def _render_copy_audit_button(project_path: Path) -> None:
    button_key = f"revive_copy_audit_{project_path}"
    if st.button(
        "Copy audit prompt",
        key=button_key,
        icon=":material/fact_check:",
        type="secondary",
        help=(
            "Second-pass gap audit. Paste into a NEW Claude Code session "
            "(fresh context is required by design — catches facts the "
            "suggest pass missed)."
        ),
    ):
        try:
            prompt = generate_audit_prompt(project_path)
        except ReviveError as exc:
            st.error(f"Could not generate audit prompt: {exc}")
            return
        st.session_state[f"{_AUDIT_KEY_PREFIX}{project_path}"] = prompt
        st.rerun()


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


def _render_pending_prompts(project_path: Path) -> None:
    suggest_key = f"{_SUGGEST_KEY_PREFIX}{project_path}"
    audit_key = f"{_AUDIT_KEY_PREFIX}{project_path}"
    suggest = st.session_state.get(suggest_key)
    audit = st.session_state.get(audit_key)

    if suggest:
        st.markdown("**Suggest prompt** — open Claude Code in this project and paste:")
        st.code(suggest, language="markdown")
        if st.button("Dismiss suggest", key=f"revive_dismiss_suggest_{project_path}"):
            st.session_state.pop(suggest_key, None)
            st.rerun()

    if audit:
        st.markdown("**Audit prompt** — open a NEW Claude Code session and paste:")
        st.code(audit, language="markdown")
        if st.button("Dismiss audit", key=f"revive_dismiss_audit_{project_path}"):
            st.session_state.pop(audit_key, None)
            st.rerun()

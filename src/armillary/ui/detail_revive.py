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

from datetime import datetime
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
from armillary.ui.detail_work import _format_age

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
    age_label = _age_label(status.last_modified)
    parts = [label, hook_label]
    if age_label:
        parts.append(age_label)
    st.caption(" · ".join(parts))

    if status.purpose_line and status.brief_state in ("configured", "stub"):
        st.markdown(f"_{status.purpose_line}_")

    if status.brief_state == "stub":
        st.info(
            "PURPOSE is filled but INVARIANTS and GOTCHAS are empty. "
            "Copy the suggest prompt and paste it in a fresh Claude Code "
            "session to fill them in.",
            icon=":material/info:",
        )
    elif status.brief_state == "unknown":
        st.warning(
            "`.revive/static.md` exists but couldn't be parsed (permission "
            "or encoding issue). Click Preview to see what `revive show` "
            "returns, then fix the file by hand.",
            icon=":material/warning:",
        )

    _render_actions(project_path, status.brief_state)


_FALLBACK_PROMPT_KEY = "_revive_fallback_prompt_"


def _age_label(last_modified: datetime | None) -> str | None:
    """Caption-friendly relative age, e.g. "updated 3d ago"."""
    if last_modified is None:
        return None
    seconds = (datetime.now() - last_modified).total_seconds()
    if seconds < 60:
        return "updated just now"
    return f"updated {_format_age(seconds)} ago"


def _render_actions(project_path: Path, brief_state: str) -> None:
    """Pair each Copy button with its own Launch on a 2-column row.

    Layout (per state):
      missing      → [ Scaffold ]
      placeholder  → [ Copy suggest | Launch ]
      stub         → [ Copy suggest | Launch ]
                     [ Preview brief        ]
      configured   → [ Copy suggest | Launch ]
                     [ Copy audit   | Launch ]
                     [ Preview brief        ]
      unknown      → [ Preview brief        ]
    """
    if brief_state == "missing":
        _render_init_button(project_path)
        _render_fallback_prompt(project_path)
        return

    if brief_state in ("placeholder", "stub", "configured"):
        col_copy, col_launch = st.columns(2)
        with col_copy:
            _render_copy_suggest_button(project_path, brief_state)
        with col_launch:
            _render_launch_claude_button(project_path, slot="suggest")

    if brief_state == "configured":
        col_copy, col_launch = st.columns(2)
        with col_copy:
            _render_copy_audit_button(project_path)
        with col_launch:
            _render_launch_claude_button(project_path, slot="audit")

    if brief_state in ("configured", "stub", "unknown"):
        _render_preview_button(project_path)

    _render_fallback_prompt(project_path)


def _render_init_button(project_path: Path) -> None:
    button_key = f"revive_init_{project_path}"
    if st.button(
        "Scaffold (`revive init`)",
        key=button_key,
        icon=":material/note_add:",
        type="primary",
        width="stretch",
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
        width="stretch",
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
        width="stretch",
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
    reruns. When ``pbcopy`` is unavailable (Linux/Windows), we keep the
    generated prompt in session_state so the user still has a way to
    grab it from a fallback text area instead of losing the result.
    """
    fallback_key = f"{_FALLBACK_PROMPT_KEY}{project_path}"
    try:
        prompt = fetch(project_path)
    except ReviveError as exc:
        st.toast(f"Could not generate {label} prompt: {exc}", icon="⚠️")
        return
    if copy_to_clipboard(prompt):
        st.session_state.pop(fallback_key, None)
        st.toast(
            f"{label.capitalize()} prompt copied — paste in a Claude tab.",
            icon="📋",
        )
    else:
        st.session_state[fallback_key] = (label, prompt)
        st.toast(
            "`pbcopy` unavailable — copy the prompt below manually.",
            icon="⚠️",
        )


def _render_fallback_prompt(project_path: Path) -> None:
    """Render the most recent prompt when clipboard copy failed.

    Lets non-macOS users (or anyone whose pbcopy is broken) still grab
    the generated prompt instead of losing it to a transient toast.
    """
    fallback_key = f"{_FALLBACK_PROMPT_KEY}{project_path}"
    payload = st.session_state.get(fallback_key)
    if payload is None:
        return
    label, prompt = payload
    st.markdown(
        f"**{label.capitalize()} prompt** — clipboard copy failed; "
        "select the text below and copy it manually:"
    )
    st.text_area(
        f"{label}_fallback",
        value=prompt,
        height=200,
        key=f"revive_fallback_{label}_{project_path}",
        label_visibility="collapsed",
    )
    if st.button(
        "Dismiss",
        key=f"revive_fallback_dismiss_{label}_{project_path}",
        type="tertiary",
    ):
        st.session_state.pop(fallback_key, None)
        st.rerun()


def _render_launch_claude_button(project_path: Path, *, slot: str = "main") -> None:
    """Streamlit forbids duplicate widget keys; ``slot`` differentiates the
    two Launch buttons that appear next to Copy suggest / Copy audit."""
    button_key = f"revive_launch_{slot}_{project_path}"
    if st.button(
        "Launch Claude (yolo)",
        key=button_key,
        icon=":material/rocket_launch:",
        type="secondary",
        width="stretch",
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
        width="stretch",
    ):
        try:
            output = revive_show(project_path)
        except ReviveError as exc:
            st.error(f"Could not run `revive show`: {exc}")
            return
        st.code(output, language="markdown")

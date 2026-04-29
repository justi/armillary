"""Revive integration section for the project detail page.

Surfaces context-revive state per project: whether `.revive/static.md`
exists, whether the brief is filled or just a placeholder, and what
hook scope is active. Provides two actions: previewing the brief that
would be injected, and saving a `revive suggest` prompt to the user-
owned scratch dir (so we never pollute the inspected project tree).

This module is a thin Streamlit renderer; all subprocess + filesystem
work lives in ``armillary.revive_service``.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from armillary.revive_runner import (
    accept_proposal,
    claude_available,
    generate_brief,
    git_is_clean,
    reject_proposal,
)
from armillary.revive_service import (
    ReviveError,
    generate_suggest_prompts,
    probe_capability,
    project_status,
    revive_show,
)

_PROMPTS_DIR = Path.home() / ".armillary" / "revive-prompts"
_BRIEF_STATE_LABELS = {
    "configured": ("✓ configured", "success"),
    "stub": ("⚠ stub — finish `revive suggest`", "warning"),
    "placeholder": ("⚠ placeholder", "warning"),
    "missing": ("✗ not set up", "muted"),
    "unknown": ("? unknown", "muted"),
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
    label, _ = _BRIEF_STATE_LABELS[status.brief_state]
    hook_label = _HOOK_SCOPE_LABELS[status.hook_scope]
    st.caption(f"{label} · {hook_label}")

    if status.purpose_line and status.brief_state in ("configured", "stub"):
        st.markdown(f"_{status.purpose_line}_")

    if status.brief_state == "stub":
        st.info(
            "PURPOSE is filled but INVARIANTS and GOTCHAS are empty — "
            "run `revive suggest` and paste the result into "
            "`.revive/static.md` to finish setup.",
            icon=":material/info:",
        )

    with st.container(horizontal=True):
        if status.brief_state in ("configured", "stub"):
            _render_preview_button(project_path)
        _render_save_prompt_button(project_path)
        _render_generate_brief_button(project_path, status.brief_state)

    _render_pending_proposal(project_path)


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


_PROPOSAL_KEY_PREFIX = "_revive_proposal_"
_SKIP_REASON_LABELS = {
    "claude_missing": "`claude` CLI not on PATH",
    "revive_missing": "`revive` CLI not on PATH",
    "not_git_repo": "project is not a git repo",
    "dirty_tree": "uncommitted changes — commit or stash first",
}


def _render_generate_brief_button(project_path: Path, brief_state: str) -> None:
    if not claude_available():
        st.caption("`claude` CLI not on PATH — install Claude Code to enable.")
        return
    dirty = not git_is_clean(project_path)
    button_key = f"revive_generate_brief_{project_path}"
    label = "Regenerate brief" if brief_state == "configured" else "Generate brief"
    help_text = (
        "Spawns headless Claude Code in this project (Read/Edit/Write only, "
        "max 10 turns) and proposes a diff for `.revive/static.md`. "
        "Cost ~$0.05–$0.20 with your Anthropic key."
    )
    if dirty:
        help_text = "Commit or stash changes first — refuses to run on dirty tree."
    if st.button(
        label,
        key=button_key,
        icon=":material/auto_awesome:",
        type="primary" if brief_state in ("missing", "stub") else "secondary",
        help=help_text,
        disabled=dirty,
    ):
        with st.spinner("Running Claude in this project — up to 3 minutes…"):
            result = generate_brief(project_path)
        st.session_state[f"{_PROPOSAL_KEY_PREFIX}{project_path}"] = result
        st.rerun()


def _render_pending_proposal(project_path: Path) -> None:
    key = f"{_PROPOSAL_KEY_PREFIX}{project_path}"
    result = st.session_state.get(key)
    if result is None:
        return

    if result.skipped_reason:
        reason_label = _SKIP_REASON_LABELS.get(
            result.skipped_reason, result.skipped_reason
        )
        st.warning(f"Skipped: {reason_label}", icon=":material/warning:")
        if st.button("Dismiss", key=f"revive_dismiss_skip_{project_path}"):
            st.session_state.pop(key, None)
            st.rerun()
        return

    if not result.success:
        st.error(f"Generation failed: {result.error}")
        if st.button("Dismiss", key=f"revive_dismiss_err_{project_path}"):
            st.session_state.pop(key, None)
            st.rerun()
        return

    if not result.diff:
        st.info(
            "Claude proposed no changes to `.revive/static.md`.",
            icon=":material/info:",
        )
        if st.button("Dismiss", key=f"revive_dismiss_nochange_{project_path}"):
            st.session_state.pop(key, None)
            st.rerun()
        return

    st.markdown("**Proposed changes to `.revive/static.md`**")
    st.code(result.diff, language="diff")
    with st.container(horizontal=True):
        if st.button(
            "Accept",
            key=f"revive_accept_{project_path}",
            icon=":material/check:",
            type="primary",
        ):
            accept_proposal(project_path)
            st.session_state.pop(key, None)
            st.success("Brief written. Open static.md to review before next session.")
            st.rerun()
        if st.button(
            "Reject",
            key=f"revive_reject_{project_path}",
            icon=":material/close:",
            type="secondary",
        ):
            reject_proposal(project_path)
            st.session_state.pop(key, None)
            st.toast("Reverted — `.revive/static.md` restored.")
            st.rerun()


def _render_save_prompt_button(project_path: Path) -> None:
    button_key = f"revive_save_prompt_{project_path}"
    if st.button(
        "Save suggest prompt",
        key=button_key,
        icon=":material/save:",
        type="secondary",
        help=(
            f"Writes `revive suggest` output to {_PROMPTS_DIR} "
            "so you can paste it into a fresh agent session."
        ),
    ):
        written = generate_suggest_prompts([project_path], output_dir=_PROMPTS_DIR)
        if not written:
            st.error(
                "Could not generate the suggest prompt. The project must be a "
                "git repo and `revive suggest` must succeed."
            )
            return
        st.success(f"Saved prompt to `{written[0]}`")

"""Project detail page — thin orchestrator for the sibling detail modules.

The detail page composes: header / glance strip / work signals /
reference grid / details expander / danger-zone archive. Each section
lives in its own module (``detail_header``, ``detail_glance``,
``detail_work``) to keep every file under the architecture target
(ADR 0001 §3). This file holds the orchestrator + the reference /
details / archive blocks that don't belong to any single section.

Re-exports ``LauncherOption`` and ``build_launcher_options`` so existing
imports (tests, tooling) keep working after the split.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from armillary.models import Project, Status
from armillary.ui.actions import go_to_overview
from armillary.ui.detail_glance import _render_glance_strip
from armillary.ui.detail_header import (
    LauncherOption,
    _render_header_tombstone,
    _render_header_with_launcher,
    build_launcher_options,
)
from armillary.ui.detail_revive import render_revive_section
from armillary.ui.detail_work import (
    _render_dirty_or_clean,
    _render_narrative_context,
    _render_recent_commits,
    _render_skip_history,
)
from armillary.ui.helpers import _load_project, _shorten_home
from armillary.ui.style import section_header

__all__ = [
    "LauncherOption",
    "build_launcher_options",
    "_render_project_detail",
    "_render_glance_strip",
]


def _render_project_detail(project_path: str) -> None:
    from armillary.ui.sidebar import _render_nav_sidebar

    _render_nav_sidebar()

    project = _load_project(project_path)
    if project is None:
        project_name = Path(project_path).name
        st.error(
            f"**{project_name}** not found in cache.\n\n"
            "The cache may be stale. Click **Reload from cache** in "
            "the sidebar, or run `armillary scan` from your terminal to "
            "re-index.",
            icon=":material/error:",
        )
        if st.button(
            "Back to overview",
            icon=":material/arrow_back:",
            type="primary",
            width="stretch",
            key="detail_back_to_overview",
        ):
            go_to_overview()
        return

    current_view = f"detail:{project_path}"
    if st.session_state.get("_arm_view") != current_view:
        for key in list(st.session_state):
            if key.startswith("_archive_confirm_"):
                st.session_state.pop(key)
    st.session_state["_arm_view"] = current_view

    if st.button(
        "← Overview",
        key="detail_back",
        type="tertiary",
    ):
        go_to_overview()

    from armillary.status_override import get_override

    override = get_override(str(project.path))
    # Override is the sole source of truth for ARCHIVED. Cache may have
    # stale ARCHIVED from a previous scan — ignore it. After Activate
    # (clear_override), override=None → not archived, even if cache
    # still says ARCHIVED until next scan.
    is_archived = override == Status.ARCHIVED

    if is_archived:
        _render_header_tombstone(project)
    else:
        _render_header_with_launcher(project)

    _render_skip_history(project)

    if is_archived:
        _render_archived_body(project)
    else:
        _render_active_body(project)

    _render_reference_section(project)
    if not is_archived:
        render_revive_section(project.path)
    _render_transition_journal(project)
    _render_details_expander(project)

    if not is_archived:
        _render_danger_zone(project)


def _render_archived_body(project: Project) -> None:
    """Minimal tombstone view — no work context for archived projects."""
    from armillary.purpose_service import get_archive_reason

    archive_reason = get_archive_reason(str(project.path))
    tombstone_msg = (
        "This project is **archived** \u2014 code is on disk but "
        "hidden from next, search, and overview."
    )
    if archive_reason:
        tombstone_msg += f"\n\nReason: *{archive_reason}*"
    st.info(tombstone_msg, icon=":material/archive:")
    if st.button(
        "Reactivate",
        key="detail_activate",
        icon=":material/unarchive:",
        type="primary",
    ):
        from armillary.status_override import clear_override

        clear_override(str(project.path))
        st.rerun()


def _render_active_body(project: Project) -> None:
    """Dirty/clean → narrative → recent work for non-archived projects."""
    import contextlib

    from armillary.context_service import get_context

    ctx = None
    if project.type.value == "git":
        with contextlib.suppress(ValueError, Exception):
            ctx = get_context(project.name)

    if ctx and ctx.is_git:
        _render_dirty_or_clean(ctx)
        _render_narrative_context(ctx)

    if project.type.value == "git":
        st.markdown("---")
        st.subheader("Recent work", anchor=False)
        skip_first = bool(ctx and ctx.recent_commits)
        _render_recent_commits(project.path, skip_first=skip_first)
        if ctx and ctx.recent_branches:
            with st.expander(
                "Recent branches",
                icon=":material/fork_right:",
                expanded=False,
            ):
                for b in ctx.recent_branches:
                    st.markdown(f"- `{b.name}` — {b.relative_time}")


def _render_reference_section(project: Project) -> None:
    """README / ADR / Notes in a 2-column grid (design spec)."""
    st.markdown(
        section_header("Reference", "docs, notes, history"),
        unsafe_allow_html=True,
    )

    md = project.metadata
    is_dormant = md and md.status in (Status.DORMANT, Status.STALLED)
    ref_col_a, ref_col_b = st.columns(2)

    with ref_col_a:
        if md and md.readme_excerpt:
            with st.expander(
                "README",
                icon=":material/description:",
                expanded=bool(is_dormant),
            ):
                st.markdown(md.readme_excerpt)
        if md and md.adr_paths:
            with st.expander(
                f"ADRs ({len(md.adr_paths)})",
                icon=":material/architecture:",
            ):
                for adr in md.adr_paths:
                    st.markdown(f"- `{adr.name}` \u2014 `{adr}`")

    with ref_col_b:
        if md and md.note_paths:
            with st.expander(
                f"Notes ({len(md.note_paths)})",
                icon=":material/note:",
            ):
                for note in md.note_paths:
                    st.markdown(f"- `{note.name}` \u2014 `{note}`")


def _render_transition_journal(project: Project) -> None:
    """Status transition history (ADR 0025)."""
    import contextlib

    with contextlib.suppress(Exception):
        from armillary.transition_service import load_journal

        journal = load_journal(str(project.path))
        if journal:
            with st.expander(
                f"Status transitions ({len(journal)})",
                icon=":material/swap_vert:",
                expanded=False,
            ):
                for entry in reversed(journal[-10:]):
                    reason = f' — "{entry["reason"]}"' if entry.get("reason") else ""
                    st.markdown(
                        f"- {entry['date']}  "
                        f"{entry['from']} \u2192 {entry['to']}{reason}"
                    )


def _render_details_expander(project: Project) -> None:
    """Collapsed details: path, umbrella, commits, work hours, size."""
    md = project.metadata
    parts = [f"`{_shorten_home(project.path)}`"]
    if md and md.commit_count is not None:
        parts.append(f"{md.commit_count} commits")
    if md and md.work_hours is not None:
        parts.append(f"{md.work_hours:.1f}h work")
    label = "Details (" + ", ".join(parts[1:]) + ")" if len(parts) > 1 else "Details"

    with st.expander(label, icon=":material/info:"):
        st.caption(f":material/folder: `{project.path}`")
        st.caption(
            f":material/inventory_2: Umbrella: `{_shorten_home(project.umbrella)}`"
        )
        st.caption(
            f":material/schedule: Last modified: "
            f"{project.last_modified.strftime('%Y-%m-%d %H:%M')}"
        )
        if md and md.last_commit_ts:
            commit_line = (
                f":material/commit: Last commit: "
                f"{md.last_commit_ts.strftime('%Y-%m-%d %H:%M')}"
            )
            if md.last_commit_author:
                commit_line += f" by {md.last_commit_author}"
            if md.last_commit_sha:
                commit_line += f" ({md.last_commit_sha[:8]})"
            st.caption(commit_line)
        if md:
            with st.container(horizontal=True):
                if md.commit_count is not None:
                    st.metric("Commits", md.commit_count, border=True)
                if md.work_hours is not None:
                    st.metric("Work h", f"{md.work_hours:.1f}", border=True)
                if md.ahead and md.ahead > 0:
                    st.metric("Ahead", md.ahead, border=True)
                if md.behind and md.behind > 0:
                    st.metric("Behind", md.behind, border=True)
                if md.size_bytes is not None:
                    st.metric("Size", _format_bytes(md.size_bytes), border=True)
                if md.file_count is not None:
                    st.metric("Files", md.file_count, border=True)


def _render_danger_zone(project: Project) -> None:
    """Unified archive box for non-archived projects (2-step confirm)."""
    confirm_key = f"_archive_confirm_{project.path}"
    confirm_archive = st.session_state.get(confirm_key, False)
    st.markdown(
        '<div class="arm-danger-zone">'
        '<div class="kicker">Danger zone \u2014 archive</div>'
        "</div>",
        unsafe_allow_html=True,
    )
    reason_bottom = st.text_input(
        "Why are you archiving?",
        placeholder="Why? (e.g. no traction, finished, pivoted)",
        key="archive_reason_bottom",
        label_visibility="collapsed",
    )
    if confirm_archive:
        st.warning("Click again to confirm archive", icon=":material/warning:")
    if st.button(
        "Archive project",
        key="detail_archive",
        icon=":material/archive:",
        type="secondary",
    ):
        if not confirm_archive:
            st.session_state[confirm_key] = True
            st.rerun()
        from armillary.purpose_service import set_archive_reason
        from armillary.status_override import set_override

        st.session_state.pop(confirm_key, None)
        set_override(str(project.path), Status.ARCHIVED)
        if reason_bottom:
            set_archive_reason(str(project.path), reason_bottom)
        st.toast(f"Archived {project.name}")
        st.rerun()


def _format_bytes(n: int) -> str:
    """Format ``n`` bytes as KB / MB / GB with one decimal place."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"

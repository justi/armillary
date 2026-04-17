"""Project detail page — narrative layout per ADR 0014.

Hierarchy: Name+Status+Open → Dirty/Clean → Branch+LastCommit →
           Recent Commits → Branches → Reference (README, Notes, ADRs, Details).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from armillary import launcher as launcher_mod
from armillary.config import Config, LauncherConfig
from armillary.models import Project, Status
from armillary.ui.actions import go_to_overview
from armillary.ui.helpers import (
    _STATUS_EMOJI,
    _load_project,
    _safe_load_config,
    _shorten_home,
)
from armillary.ui.launcher_support import detect_launcher_compat


@dataclass(frozen=True)
class LauncherOption:
    """View-model for one entry in the launcher dropdown."""

    target_id: str
    label: str
    availability_mode: str
    detail: str | None = None


def build_launcher_options(
    launchers: dict[str, LauncherConfig],
) -> tuple[list[LauncherOption], list[str], list[str], list[str]]:
    """Filter terminal launchers, detect availability, build display options."""
    available: list[LauncherOption] = []
    missing_labels: list[str] = []
    terminal_only_labels: list[str] = []
    app_labels: list[str] = []
    for target_id, launcher_cfg in launchers.items():
        label = (
            f"{launcher_cfg.icon + ' ' if launcher_cfg.icon else ''}"
            f"{launcher_cfg.label}"
        )
        if launcher_cfg.terminal:
            terminal_only_labels.append(launcher_cfg.label)
            continue
        availability = detect_launcher_compat(launcher_cfg)
        if availability.available:
            available.append(
                LauncherOption(
                    target_id=target_id,
                    label=label,
                    availability_mode=availability.mode,
                    detail=availability.detail,
                )
            )
            if availability.mode == "macos-app":
                app_labels.append(label)
        else:
            missing_labels.append(label)
    return available, missing_labels, terminal_only_labels, app_labels


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

    md = project.metadata

    # Back navigation in content area (not just sidebar)
    if st.button(
        "← Overview",
        key="detail_back",
        type="tertiary",
    ):
        go_to_overview()

    # --- Row 1: Name + Status + Launcher (top-right) ---
    _render_header_with_launcher(project)

    # --- Row 2: Dirty/Clean signal ---
    import contextlib

    from armillary.context_service import get_context

    ctx = None
    if project.type.value == "git":
        with contextlib.suppress(ValueError, Exception):
            ctx = get_context(project.name)

    if ctx and ctx.is_git:
        _render_dirty_or_clean(ctx)

    # --- Row 3: Branch + Last commit narrative ---
    if ctx and ctx.is_git:
        _render_narrative_context(ctx)

    # --- Section: What I was working on ---
    if project.type.value == "git":
        st.markdown("---")
        st.subheader("What I was working on", anchor=False)
        # Skip first commit — already shown in narrative context above
        skip_first = bool(ctx and ctx.recent_commits)
        _render_recent_commits(project.path, skip_first=skip_first)
        if ctx and ctx.recent_branches:
            with st.expander(
                "Recent branches", icon=":material/fork_right:", expanded=False
            ):
                for b in ctx.recent_branches:
                    st.markdown(f"- `{b.name}` — {b.relative_time}")

    # --- Section: Reference ---
    st.markdown("---")
    st.subheader("Reference", anchor=False)

    is_dormant = md and md.status in (Status.DORMANT, Status.PAUSED)

    if md and md.readme_excerpt:
        with st.expander(
            "README",
            icon=":material/description:",
            expanded=bool(is_dormant),
        ):
            st.markdown(md.readme_excerpt)

    if md and md.note_paths:
        with st.expander(f"Notes ({len(md.note_paths)})", icon=":material/note:"):
            for note in md.note_paths:
                st.markdown(f"- `{note.name}` \u2014 `{note}`")

    if md and md.adr_paths:
        with st.expander(
            f"ADRs ({len(md.adr_paths)})",
            icon=":material/architecture:",
        ):
            for adr in md.adr_paths:
                st.markdown(f"- `{adr.name}` \u2014 `{adr}`")

    # --- Collapsed details (path, umbrella, stats) ---
    _render_details_expander(project)


def _render_header_with_launcher(project: Project) -> None:
    """Name + status badge + velocity trend + launcher dropdown."""
    md = project.metadata
    status_str = ""
    if md and md.status:
        emoji = _STATUS_EMOJI.get(md.status.value, "\u00b7")
        status_str = f" — {emoji} {md.status.value}"

    # S1: velocity trend
    trend_labels = {
        "rising": ":material/trending_up: trending up",
        "falling": ":material/trending_down: trending down",
        "flat": ":material/trending_flat: steady",
        "dead": ":material/block: no recent activity",
    }
    trend_str = ""
    if md and md.velocity_trend:
        trend_str = f" · {trend_labels.get(md.velocity_trend, md.velocity_trend)}"

    col_title, col_launcher = st.columns([5, 2])
    with col_title:
        st.title(f"{project.name}{status_str}")
        # S5: project age + intensity
        _render_project_age(md)
    with col_launcher:
        cfg = _safe_load_config()
        if cfg is not None and cfg.launchers:
            _render_launcher_compact(project, cfg)

    if trend_str:
        st.caption(trend_str)


def _render_launcher_compact(project: Project, cfg: Config) -> None:
    """Compact launcher: selectbox + Open button, top-right."""
    available, missing_labels, terminal_only_labels, app_labels = (
        build_launcher_options(cfg.launchers)
    )

    if terminal_only_labels:
        st.caption(
            f"Terminal: {', '.join(terminal_only_labels)} "
            "\u2014 use CLI `armillary open`"
        )

    if not available:
        return

    options_map = {opt.target_id: opt.label for opt in available}
    target_id = st.selectbox(
        "Launcher",
        options=list(options_map),
        format_func=lambda tid: options_map[tid],
        label_visibility="collapsed",
        key=f"launcher_pick_{project.path}",
    )
    clicked = st.button(
        "Open",
        icon=":material/launch:",
        width="stretch",
        key=f"launcher_open_{project.path}",
        type="primary",
    )

    if clicked:
        result = launcher_mod.launch(project, target_id, launchers=cfg.launchers)
        if result.ok:
            st.success(f"Opened in `{target_id}`.")
        else:
            st.error(result.error or "Launch failed.")


def _render_dirty_or_clean(ctx: object) -> None:
    """Full-width dirty warning or clean success signal."""
    if ctx.dirty_count > 0:
        s = "s" if ctx.dirty_count > 1 else ""
        # S3: dirty file age
        age_hint = ""
        if ctx.dirty_max_age_seconds is not None:
            age_hint = f" \u2014 oldest {_format_age(ctx.dirty_max_age_seconds)}"
        st.warning(
            f"**{ctx.dirty_count} dirty file{s}{age_hint}** "
            "\u2014 commit or stash before switching",
            icon=":material/edit_note:",
        )
        with st.expander("Dirty files", expanded=ctx.dirty_count <= 5):
            for f in ctx.dirty_files:
                st.code(f, language=None)
            if ctx.dirty_count > len(ctx.dirty_files):
                more = ctx.dirty_count - len(ctx.dirty_files)
                st.caption(f"and {more} more")
    else:
        st.success("Clean working tree", icon=":material/check_circle:")


def _render_narrative_context(ctx: object) -> None:
    """Branch + last commit + session + branch/remote as narrative lines."""
    if ctx.branch:
        st.markdown(f"Branch: `{ctx.branch}`")
    if ctx.recent_commits:
        c = ctx.recent_commits[0]
        st.markdown(
            f"Last: {c.relative_time} \u2014 `{c.short_hash}` \u201c{c.subject}\u201d"
        )
    # S4: last session
    if ctx.last_session is not None:
        dur = ctx.last_session.duration_seconds
        if dur >= 3600:
            dur_str = f"{dur / 3600:.1f}h"
        elif dur >= 60:
            dur_str = f"{dur / 60:.0f}min"
        else:
            dur_str = "<1min"
        st.markdown(
            f"Last session: **{dur_str}**, "
            f"{ctx.last_session.commit_count} commit(s), "
            f"{ctx.last_session.ended_relative}"
        )
    # S6: branch count + remote warning
    parts: list[str] = []
    if ctx.branch_count is not None and ctx.branch_count > 1:
        parts.append(f"{ctx.branch_count} local branches")
    if ctx.has_remote is False:
        parts.append("**no remote \u2014 push before archiving**")
    if parts:
        st.caption(" \u00b7 ".join(parts))


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


def _render_recent_commits(
    repo_path: Path, limit: int = 5, *, skip_first: bool = False
) -> None:
    """Show the last commits in an expanded expander."""
    commits = _git_log_recent(repo_path, limit=limit + (1 if skip_first else 0))
    if skip_first:
        commits = commits[1:]
    if not commits:
        st.caption("_No commit history available._")
        return

    with st.expander("Recent commits", expanded=True):
        for commit in commits:
            st.markdown(
                f"- **`{commit['sha']}`** \u2014 {commit['message']}  \n"
                f"  _{commit['date']} \u00b7 {commit['author']}_"
            )


def _git_log_recent(repo_path: Path, *, limit: int = 5) -> list[dict[str, str]]:
    try:
        proc = subprocess.run(
            [
                "git",
                "log",
                f"-{limit}",
                "--no-merges",
                "--format=%h\x1f%s\x1f%ar\x1f%an",
            ],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []

    commits: list[dict[str, str]] = []
    for line in proc.stdout.strip().splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        commits.append(
            {
                "sha": parts[0],
                "message": parts[1],
                "date": parts[2],
                "author": parts[3],
            }
        )
    return commits


def _format_age(seconds: float) -> str:
    """Human-readable age from seconds."""
    if seconds < 3600:
        return f"{seconds / 60:.0f}min"
    if seconds < 86400:
        return f"{seconds / 3600:.0f}h"
    days = seconds / 86400
    if days < 30:
        return f"{days:.0f}d"
    return f"{days / 30:.0f}mo"


def _render_project_age(md: object | None) -> None:
    """S5: Show project age + work intensity below the title.

    Intensity = work_hours / active_span (first→last commit).
    Only shown when active span >= 30 days — shorter spans produce
    misleading h/mo values.
    """
    if md is None or not md.first_commit_ts or not md.work_hours:
        return
    from datetime import datetime

    age_days = (datetime.now() - md.first_commit_ts).days
    if age_days <= 0:
        return
    if age_days >= 365:
        age_str = f"{age_days / 365:.1f}y"
    elif age_days >= 30:
        age_str = f"{age_days / 30.44:.0f}mo"
    else:
        age_str = f"{age_days}d"
    # Intensity: h/mo over active span (first→last commit), not first→now
    intensity_str = ""
    if md.last_commit_ts and md.first_commit_ts:
        span_days = max((md.last_commit_ts - md.first_commit_ts).days, 1)
        if span_days >= 30:
            span_months = span_days / 30.44
            intensity = md.work_hours / span_months
            intensity_str = f" \u00b7 {intensity:.1f} h/mo"
    st.caption(f"Age {age_str}{intensity_str}")


def _format_bytes(n: int) -> str:
    """Format `n` bytes as KB / MB / GB with one decimal place."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"

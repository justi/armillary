"""Project detail page — single-project view with metadata and actions."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from armillary import launcher as launcher_mod
from armillary.config import Config, LauncherConfig
from armillary.models import Project
from armillary.ui.helpers import (
    _STATUS_EMOJI,
    _load_project,
    _safe_load_config,
    _shorten_home,
)


@dataclass(frozen=True)
class LauncherOption:
    """View-model for one entry in the launcher dropdown."""

    target_id: str
    label: str
    availability_mode: str
    detail: str | None = None


@dataclass(frozen=True)
class _LauncherAvailabilityCompat:
    available: bool
    mode: str
    detail: str | None = None
    app_name: str | None = None


def _detect_launcher_compat(config: LauncherConfig) -> _LauncherAvailabilityCompat:
    """Use the new launcher detection when available, else fall back to PATH."""
    detect = getattr(launcher_mod, "detect_launcher", None)
    if callable(detect):
        return detect(config)
    resolved = shutil.which(config.command)
    return _LauncherAvailabilityCompat(
        available=resolved is not None,
        mode="path" if resolved is not None else "missing",
        detail=resolved,
    )


def build_launcher_options(
    launchers: dict[str, LauncherConfig],
) -> tuple[list[LauncherOption], list[str], list[str], list[str]]:
    """Filter terminal launchers, detect availability, build display options.

    Returns ``(available, missing_labels, terminal_only_labels, app_labels)``.
    """
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
        availability = _detect_launcher_compat(launcher_cfg)
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
            "The cache may be stale. Click **🔄 Reload from cache** in "
            "the sidebar, or run `armillary scan` from your terminal to "
            "re-index."
        )
        return

    md = project.metadata

    st.title(project.name)

    _render_detail_metric_tiles(project)

    st.divider()
    _render_detail_captions(project)

    # PLAN.md S5: "Open in…" dropdown wired to launcher catalogue.
    cfg = _safe_load_config()
    if cfg is not None:
        st.divider()
        st.subheader("Open in…")
        _render_launcher_dropdown(project, cfg)

    if md and md.readme_excerpt:
        st.divider()
        st.subheader("README")
        st.info(md.readme_excerpt)

    if project.type.value == "git":
        st.divider()
        st.subheader("Recent commits")
        _render_recent_commits(project.path)

    if md and md.note_paths:
        st.divider()
        st.subheader(f"Notes ({len(md.note_paths)})")
        for note in md.note_paths:
            st.markdown(f"- `{note.name}` — `{note}`")

    if md and md.adr_paths:
        st.divider()
        st.subheader(f"Architecture Decision Records ({len(md.adr_paths)})")
        for adr in md.adr_paths:
            st.markdown(f"- `{adr.name}` — `{adr}`")


def _render_detail_metric_tiles(project: Project) -> None:
    md = project.metadata
    metric_cols = st.columns(4)

    if md and md.status:
        emoji = _STATUS_EMOJI.get(md.status.value, "·")
        metric_cols[0].metric("Status", f"{emoji} {md.status.value}")
    else:
        metric_cols[0].metric("Status", "—")
    metric_cols[1].metric("Type", project.type.value)
    if md and md.branch:
        metric_cols[2].metric("Branch", md.branch)
    if md and md.dirty_count is not None:
        metric_cols[3].metric("Dirty files", md.dirty_count)

    # Second row: commits, work hours, ahead, behind.
    if md and any(
        x is not None for x in (md.commit_count, md.work_hours, md.ahead, md.behind)
    ):
        row2 = st.columns(4)
        if md.commit_count is not None:
            row2[0].metric("Commits", md.commit_count)
        if md.work_hours is not None:
            row2[1].metric("Work h", f"{md.work_hours:.1f}")
        if md.ahead is not None:
            row2[2].metric("Ahead", md.ahead)
        if md.behind is not None:
            row2[3].metric("Behind", md.behind)

    # Third row: size / file count.
    if md and any(x is not None for x in (md.size_bytes, md.file_count)):
        row3 = st.columns(4)
        if md.size_bytes is not None:
            row3[0].metric("Size", _format_bytes(md.size_bytes))
        if md.file_count is not None:
            row3[1].metric("Files", md.file_count)


def _render_detail_captions(project: Project) -> None:
    md = project.metadata
    st.caption(f"📁 `{project.path}`")
    st.caption(f"📦 Umbrella: `{_shorten_home(project.umbrella)}`")
    st.caption(f"🕐 Last modified: {project.last_modified.strftime('%Y-%m-%d %H:%M')}")
    if md and md.last_commit_ts:
        commit_line = f"📝 Last commit: {md.last_commit_ts.strftime('%Y-%m-%d %H:%M')}"
        if md.last_commit_author:
            commit_line += f" by {md.last_commit_author}"
        if md.last_commit_sha:
            commit_line += f" ({md.last_commit_sha[:8]})"
        st.caption(commit_line)


def _render_launcher_dropdown(project: Project, cfg: Config) -> None:
    """PLAN.md S5: '"Open in…" dropdown per project — driven by yaml config'.

    Each non-terminal entry from `cfg.launchers` is shown with its
    label/icon. Click -> calls `launcher.launch()` and surfaces
    success/error inline.

    **Terminal launchers are excluded** from the dashboard. Their
    `subprocess.run()` path inherits the parent's stdio (necessary
    for interactive `codex` / `claude-code` sessions) which would
    block the Streamlit server thread and commandeer the terminal
    that hosts the dashboard process. Use them from the CLI instead
    via `armillary open <name> -t <target>`.
    """
    if not cfg.launchers:
        st.caption("No launchers configured.")
        return

    available, missing_labels, terminal_only_labels, app_labels = (
        build_launcher_options(cfg.launchers)
    )

    # P2.8: Surface terminal-only info ABOVE the dropdown so it is not
    # buried below the fold.
    if terminal_only_labels:
        st.info(
            f"Terminal-only launchers ({', '.join(terminal_only_labels)}) "
            "are interactive — use `armillary open <name> -t <id>` "
            "from your terminal."
        )

    if not available:
        st.warning(
            "No GUI launchers were detected. armillary checks both CLI tools on PATH "
            "and known macOS app bundles."
        )
        if missing_labels:
            st.caption(f"Configured but missing: {', '.join(missing_labels)}")
        return

    options_map = {opt.target_id: opt.label for opt in available}
    col_select, col_btn = st.columns([3, 1])
    with col_select:
        target_id = st.selectbox(
            "Launcher",
            options=list(options_map),
            format_func=lambda tid: options_map[tid],
            label_visibility="collapsed",
            key=f"launcher_pick_{project.path}",
        )
    with col_btn:
        clicked = st.button(
            "🚀 Open",
            use_container_width=True,
            key=f"launcher_open_{project.path}",
        )

    if clicked:
        result = launcher_mod.launch(project, target_id, launchers=cfg.launchers)
        if result.ok:
            st.success(f"Opened in `{target_id}`.")
        else:
            st.error(result.error or "Launch failed.")

    if app_labels:
        st.caption(f"Detected via macOS app bundle: {', '.join(app_labels)}")
    if missing_labels:
        st.caption(f"Still not detected: {', '.join(missing_labels)}")


def _render_recent_commits(repo_path: Path, limit: int = 5) -> None:
    """Show the last `limit` commits as a markdown list.

    Calls `git log` directly via subprocess — much cheaper than going
    through GitPython for a one-off display, and we already have a
    timeout pattern from `LiteralSearch`.
    """
    commits = _git_log_recent(repo_path, limit=limit)
    if not commits:
        st.caption("_No commit history available._")
        return

    for commit in commits:
        st.markdown(
            f"- **`{commit['sha']}`** — {commit['message']}  \n"
            f"  _{commit['date']} · {commit['author']}_"
        )


def _git_log_recent(repo_path: Path, *, limit: int = 5) -> list[dict[str, str]]:
    try:
        proc = subprocess.run(
            [
                "git",
                "log",
                f"-{limit}",
                "--no-merges",
                "--format=%h\x1f%s\x1f%ci\x1f%an",
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


def _format_bytes(n: int) -> str:
    """Format `n` bytes as KB / MB / GB with one decimal place."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"

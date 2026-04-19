"""Work signals on the detail page: dirty/clean banner, narrative
context line, recent commits timeline, skip history. Split out of
``detail.py`` for the 400-line architecture target.
"""

from __future__ import annotations

import html as _html
import subprocess
from pathlib import Path

import streamlit as st

from armillary.models import Project

_PORCELAIN_MAP = {
    "??": "new     ",
    " M": "modified",
    "M ": "staged  ",
    " D": "deleted ",
    "D ": "deleted ",
    "A ": "added   ",
    "MM": "modified",
    "AM": "added   ",
}


def _humanize_porcelain(line: str) -> str:
    """Translate git status porcelain prefixes to human-readable labels."""
    if len(line) >= 3:
        prefix = line[:2]
        path = line[3:]
        label = _PORCELAIN_MAP.get(prefix, prefix)
        return f"{label} {path}"
    return line


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


def _render_dirty_or_clean(ctx: object) -> None:
    """Full-width dirty warning or clean success signal."""
    if ctx.dirty_count > 0:
        s = "s" if ctx.dirty_count > 1 else ""
        age_hint = ""
        if ctx.dirty_max_age_seconds is not None:
            age_hint = f" \u2014 {_format_age(ctx.dirty_max_age_seconds)} stale"
        st.warning(
            f"**{ctx.dirty_count} uncommitted file{s}{age_hint}**",
            icon=":material/edit_note:",
        )
        with st.expander("Uncommitted files", expanded=ctx.dirty_count <= 5):
            for f in ctx.dirty_files:
                st.code(_humanize_porcelain(f), language=None)
            if ctx.dirty_count > len(ctx.dirty_files):
                more = ctx.dirty_count - len(ctx.dirty_files)
                st.caption(f"and {more} more")
    else:
        st.success("No uncommitted work", icon=":material/check_circle:")


def _render_narrative_context(ctx: object) -> None:
    """Branch + last commit + session + branch/remote as narrative lines."""
    if ctx.branch:
        st.markdown(f"Branch: `{ctx.branch}`")
    if ctx.recent_commits:
        c = ctx.recent_commits[0]
        st.markdown(
            f"Last: {c.relative_time} \u2014 `{c.short_hash}` \u201c{c.subject}\u201d"
        )
    if ctx.last_session is not None:
        dur = ctx.last_session.duration_seconds
        if dur >= 3600:
            dur_str = f"{dur / 3600:.1f}h"
        elif dur >= 60:
            dur_str = f"{dur / 60:.0f}min"
        else:
            dur_str = "<1min"
        n = ctx.last_session.commit_count
        c_word = "commit" if n == 1 else "commits"
        st.markdown(
            f"Last session: **{dur_str}**, "
            f"{n} {c_word}, "
            f"{ctx.last_session.ended_relative}"
        )
    parts: list[str] = []
    if ctx.branch_count is not None and ctx.branch_count > 1:
        parts.append(f"{ctx.branch_count} local branches")
    if ctx.unmerged_branches:
        n = len(ctx.unmerged_branches)
        parts.append(f"**{n} unmerged**")
    if ctx.has_remote is False:
        parts.append("**no remote \u2014 push before archiving**")
    if parts:
        st.caption(" \u00b7 ".join(parts))
    if ctx.unmerged_branches:
        with st.expander(
            f"{len(ctx.unmerged_branches)} unmerged branches",
            icon=":material/fork_right:",
            expanded=False,
        ):
            for b in ctx.unmerged_branches:
                st.markdown(f"- `{b}`")


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


def _render_recent_commits(
    repo_path: Path, limit: int = 5, *, skip_first: bool = False
) -> None:
    """Show the last commits as a vertical timeline (design bundle change)."""
    commits = _git_log_recent(repo_path, limit=limit + (1 if skip_first else 0))
    if skip_first:
        commits = commits[1:]
    if not commits:
        st.caption("_No commit history available._")
        return

    parts = ['<div class="arm-timeline">']
    for i, c in enumerate(commits):
        latest_cls = " latest" if i == 0 else ""
        parts.append(
            f'<div class="arm-timeline-item{latest_cls}">'
            '<div class="dot"></div>'
            '<div class="line">'
            f'<code class="sha">{_html.escape(c["sha"])}</code>'
            f'<span class="msg">{_html.escape(c["message"])}</span>'
            "</div>"
            '<div class="meta">'
            f"{_html.escape(c['date'])} \u00b7 {_html.escape(c['author'])}"
            "</div></div>"
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def _render_skip_history(project: Project) -> None:
    """Show skip history if this project was previously skipped."""
    from armillary.next_service import _load_skips

    skips = _load_skips()
    entry = skips.get(str(project.path))
    if not entry:
        return
    count = entry.get("count", 0)
    reason = entry.get("reason")
    if count <= 0:
        return
    parts = [f"Skipped {count}x from suggestions"]
    if reason:
        parts.append(f"last reason: *{reason}*")
    st.info(" \u2014 ".join(parts), icon=":material/skip_next:")

"""Context restoration for project re-entry.

Answers "where was I?" by gathering live git state + cached metadata.
All operations are local — must complete in sub-second.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .cache import Cache
from .utils import (
    find_projects_by_name,
    resolve_project_by_name,
    summarize_project_matches,
)


@dataclass(frozen=True)
class ProjectContext:
    """Everything needed to resume work on a project."""

    # From cache
    name: str
    path: Path
    status: str | None
    work_hours: float | None

    # Live from git
    branch: str | None = None
    dirty_files: list[str] = field(default_factory=list)
    dirty_count: int = 0
    recent_commits: list[CommitInfo] = field(default_factory=list)
    recent_branches: list[BranchInfo] = field(default_factory=list)

    # Decision signals (ADR 0017) — live signals.
    dirty_max_age_seconds: float | None = None
    last_session: SessionInfo | None = None

    # Decision signals (ADR 0017) — from cache.
    velocity_trend: str | None = None
    commit_velocity: list[int] | None = None
    monthly_commits: list[int] | None = None
    first_commit_ts: str | None = None  # ISO format for display
    last_commit_ts_iso: str | None = None  # ISO format — for intensity calc
    branch_count: int | None = None
    has_remote: bool | None = None

    # Live — unmerged branches
    unmerged_branches: list[str] = field(default_factory=list)

    # From cache — for display.
    readme_oneliner: str | None = None

    # Flags
    is_git: bool = True


@dataclass(frozen=True)
class CommitInfo:
    short_hash: str
    relative_time: str
    subject: str


@dataclass(frozen=True)
class SessionInfo:
    """Last continuous work session derived from commit timestamps."""

    duration_seconds: float
    commit_count: int
    ended_relative: str  # e.g. "2 hours ago"


@dataclass(frozen=True)
class BranchInfo:
    name: str
    relative_time: str


def get_context(
    project_name: str,
    *,
    db_path: Path | None = None,
) -> ProjectContext | None:
    """Build context for a project by name (substring match).

    Returns None if no project matches. Raises ValueError if ambiguous.
    """
    with Cache(db_path=db_path) as cache:
        projects = cache.list_projects()

    try:
        project = resolve_project_by_name(projects, project_name)
    except ValueError:
        matches = find_projects_by_name(projects, project_name)
        raise ValueError(f"Ambiguous: {summarize_project_matches(matches)}") from None

    if project is None:
        return None
    md = project.metadata
    # Check manual override before cached status
    from .status_override import get_override

    override = get_override(str(project.path))
    if override is not None:
        status = override.value
    else:
        status = md.status.value if md and md.status else None
    work_hours = md.work_hours if md else None

    # Cached decision signals (ADR 0017)
    velocity_trend = md.velocity_trend if md else None
    commit_velocity = md.commit_velocity if md else None
    monthly_commits = md.monthly_commits if md else None
    first_commit_ts = (
        md.first_commit_ts.isoformat() if md and md.first_commit_ts else None
    )
    last_commit_ts_iso = (
        md.last_commit_ts.isoformat() if md and md.last_commit_ts else None
    )
    branch_count = md.branch_count if md else None
    has_remote = md.has_remote if md else None
    # README one-liner for context display
    readme_oneliner = None
    if md and md.readme_excerpt:
        # First sentence or first 80 chars
        excerpt = md.readme_excerpt
        dot = excerpt.find(". ")
        if dot > 0 and dot < 80:
            readme_oneliner = excerpt[: dot + 1]
        else:
            readme_oneliner = excerpt[:80] + ("..." if len(excerpt) > 80 else "")

    # Detect git repo: .git can be a directory (normal) or file (worktree/submodule)
    if not (project.path / ".git").exists():
        return ProjectContext(
            name=project.name,
            path=project.path,
            status=status,
            work_hours=work_hours,
            is_git=False,
        )

    branch = _current_branch(project.path)
    dirty_files, dirty_count = _dirty_state(project.path)
    return ProjectContext(
        name=project.name,
        path=project.path,
        status=status,
        work_hours=work_hours,
        branch=branch,
        dirty_files=dirty_files,
        dirty_count=dirty_count,
        recent_commits=_recent_commits(project.path),
        recent_branches=_recent_branches(project.path, current=branch),
        dirty_max_age_seconds=_dirty_max_age(project.path, dirty_files),
        last_session=_last_session(project.path),
        unmerged_branches=_unmerged_branches(project.path),
        velocity_trend=velocity_trend,
        commit_velocity=commit_velocity,
        monthly_commits=monthly_commits,
        first_commit_ts=first_commit_ts,
        last_commit_ts_iso=last_commit_ts_iso,
        branch_count=branch_count,
        has_remote=has_remote,
        readme_oneliner=readme_oneliner,
        is_git=True,
    )


def _run_git(project_path: Path, *args: str) -> str:
    """Run a git command and return stdout. Empty string on failure.

    Only strips trailing whitespace — leading spaces are meaningful
    for commands like `git status --porcelain`.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.rstrip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _current_branch(path: Path) -> str | None:
    branch = _run_git(path, "rev-parse", "--abbrev-ref", "HEAD")
    return branch.strip() or None


def _dirty_state(path: Path, *, max_show: int = 5) -> tuple[list[str], int]:
    """Return (file_list, total_count) from a single git status call."""
    output = _run_git(path, "status", "--porcelain", "--no-renames")
    if not output:
        return [], 0
    lines = output.splitlines()
    return [line.rstrip() for line in lines[:max_show]], len(lines)


def _recent_commits(path: Path, *, count: int = 5) -> list[CommitInfo]:
    output = _run_git(
        path,
        "log",
        f"-{count}",
        "--format=%h\t%ar\t%s",
    )
    if not output:
        return []
    commits = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append(
                CommitInfo(
                    short_hash=parts[0],
                    relative_time=parts[1],
                    subject=parts[2],
                )
            )
    return commits


def _recent_branches(
    path: Path, *, current: str | None = None, count: int = 3
) -> list[BranchInfo]:
    output = _run_git(
        path,
        "branch",
        "--sort=-committerdate",
        "--format=%(refname:short)\t%(committerdate:relative)",
    )
    if not output:
        return []
    branches = []
    for line in output.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            name = parts[0]
            if name == current or name == "HEAD":
                continue
            branches.append(BranchInfo(name=name, relative_time=parts[1]))
        if len(branches) >= count:
            break
    return branches


def _unmerged_branches(project_path: Path) -> list[str]:
    """Return names of branches not merged into HEAD."""
    output = _run_git(project_path, "branch", "--no-merged", "HEAD")
    if not output:
        return []
    return [b.strip().lstrip("* ") for b in output.splitlines() if b.strip()]


# --- decision signals (ADR 0017) — live, not cached -------------------------

_SESSION_GAP_SECONDS = 2 * 3600  # 2h gap = session boundary


def _dirty_max_age(project_path: Path, dirty_files: list[str]) -> float | None:
    """Return age in seconds of the oldest dirty file, or None if clean."""
    import time

    if not dirty_files:
        return None
    now = time.time()
    oldest = now
    for line in dirty_files:
        # porcelain format: "XY filename" — strip status prefix
        rel = line[3:] if len(line) > 3 else line
        full = project_path / rel
        try:
            mtime = full.stat().st_mtime
            oldest = min(oldest, mtime)
        except OSError:
            continue
    if oldest >= now:
        return None
    return now - oldest


def _last_session(project_path: Path) -> SessionInfo | None:
    """Derive the last continuous work session from recent git commits.

    A session = consecutive commits with gaps < 2h between them.
    Returns info about the most recent such session.
    """
    output = _run_git(
        project_path,
        "log",
        "-50",
        "--format=%at\t%ar",
    )
    if not output:
        return None

    entries: list[tuple[int, str]] = []
    for line in output.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            try:
                entries.append((int(parts[0]), parts[1]))
            except ValueError:
                continue

    if not entries:
        return None

    # Entries are newest-first from git log. Walk from newest to find
    # where the session boundary is.
    session_commits = [entries[0]]
    for i in range(1, len(entries)):
        gap = entries[i - 1][0] - entries[i][0]
        if gap < _SESSION_GAP_SECONDS:
            session_commits.append(entries[i])
        else:
            break

    duration = session_commits[0][0] - session_commits[-1][0]
    return SessionInfo(
        duration_seconds=float(duration),
        commit_count=len(session_commits),
        ended_relative=session_commits[0][1],
    )

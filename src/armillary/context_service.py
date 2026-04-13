"""Context restoration for project re-entry.

Answers "where was I?" by gathering live git state + cached metadata.
All operations are local — must complete in sub-second.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .cache import Cache


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

    # Flags
    is_git: bool = True


@dataclass(frozen=True)
class CommitInfo:
    short_hash: str
    relative_time: str
    subject: str


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

    matches = [p for p in projects if project_name.lower() in p.name.lower()]

    if not matches:
        return None
    if len(matches) > 1:
        exact = [p for p in matches if p.name.lower() == project_name.lower()]
        if len(exact) == 1:
            matches = exact
        else:
            names = ", ".join(p.name for p in matches[:5])
            suffix = f" (+{len(matches) - 5} more)" if len(matches) > 5 else ""
            raise ValueError(f"Ambiguous: {names}{suffix}")

    project = matches[0]
    md = project.metadata
    status = md.status.value if md and md.status else None
    work_hours = md.work_hours if md else None

    if not (project.path / ".git").is_dir():
        return ProjectContext(
            name=project.name,
            path=project.path,
            status=status,
            work_hours=work_hours,
            is_git=False,
        )

    return ProjectContext(
        name=project.name,
        path=project.path,
        status=status,
        work_hours=work_hours,
        branch=_current_branch(project.path),
        dirty_files=_dirty_files(project.path),
        dirty_count=_dirty_count(project.path),
        recent_commits=_recent_commits(project.path),
        recent_branches=_recent_branches(project.path),
        is_git=True,
    )


def _run_git(project_path: Path, *args: str) -> str:
    """Run a git command and return stdout. Empty string on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _current_branch(path: Path) -> str | None:
    branch = _run_git(path, "rev-parse", "--abbrev-ref", "HEAD")
    return branch or None


def _dirty_files(path: Path, *, max_show: int = 5) -> list[str]:
    output = _run_git(path, "status", "--porcelain", "--no-renames")
    if not output:
        return []
    lines = output.splitlines()
    return [line.strip() for line in lines[:max_show]]


def _dirty_count(path: Path) -> int:
    output = _run_git(path, "status", "--porcelain", "--no-renames")
    if not output:
        return 0
    return len(output.splitlines())


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


def _recent_branches(path: Path, *, count: int = 3) -> list[BranchInfo]:
    current = _current_branch(path)
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

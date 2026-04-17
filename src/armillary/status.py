"""Status heuristics — turn metadata + filesystem signals into a label.

The labels answer "where does this project stand?":

- ACTIVE       — recent commit OR recent file edit (default ≤ 7 days)
- STALLED       — dirty working tree, last commit between 7 and 30 days
- DORMANT      — nothing has happened for 30+ days
- IDEA         — loose folder with no git history
- IN_PROGRESS  — IDEA folder containing a TODO.md with open `[ ]` items

The function is pure on its inputs (`project`, `now`) except for one
filesystem read of `TODO.md` for IDEA folders. Tests inject `now` so
the heuristic can be exercised at any wall-clock time.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .models import Project, ProjectType, Status

DEFAULT_ACTIVE_DAYS = 7
DEFAULT_STALLED_DAYS = 30


def compute_status(
    project: Project,
    *,
    now: datetime | None = None,
    active_days: int = DEFAULT_ACTIVE_DAYS,
    paused_days: int = DEFAULT_STALLED_DAYS,
) -> Status:
    """Classify a single project.

    Manual overrides (e.g. ARCHIVED) take precedence over heuristics.
    """
    from .status_override import get_override

    override = get_override(str(project.path))
    if override is not None:
        return override

    now = now or datetime.now()

    if project.type is ProjectType.IDEA:
        if _has_open_todo(project.path):
            return Status.IN_PROGRESS
        return Status.IDEA

    # Git project: prefer last commit time, fall back to filesystem mtime
    # for repos where GitPython could not extract metadata.
    md = project.metadata
    last_commit = md.last_commit_ts if md is not None else None
    last_signal = last_commit or project.last_modified
    age = now - last_signal

    dirty_count = (md.dirty_count if md is not None else None) or 0

    if age <= timedelta(days=active_days):
        return Status.ACTIVE
    if dirty_count > 0 and age <= timedelta(days=paused_days):
        return Status.STALLED
    # Anything older than active_days with no dirty work in progress is
    # DORMANT — including the 7-30 day "clean and quiet" window. STALLED
    # is reserved for "I left work in progress and walked away".
    return Status.DORMANT


def _has_open_todo(project_path: Path) -> bool:
    """True if a TODO.md exists with at least one unchecked checkbox."""
    todo = project_path / "TODO.md"
    if not todo.is_file():
        return False
    try:
        content = todo.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "[ ]" in content

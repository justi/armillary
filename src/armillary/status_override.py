"""Manual status overrides — survive re-scans.

When a user runs `armillary archive <project>`, the override is stored
here. `compute_status()` checks overrides BEFORE applying heuristics,
so an ARCHIVED project stays ARCHIVED regardless of git activity.

Storage: JSON file next to the cache DB, same pattern as excluded.json.
"""

from __future__ import annotations

from pathlib import Path

from .cache import default_db_path
from .models import Status
from .utils import load_json_str_dict, write_json_file

_OVERRIDES_FILENAME = "status-overrides.json"


def _overrides_path() -> Path:
    return default_db_path().parent / _OVERRIDES_FILENAME


def load_overrides() -> dict[str, str]:
    """Load {project_path: status_string} from disk."""
    return load_json_str_dict(_overrides_path())


def _save_overrides(overrides: dict[str, str]) -> None:
    write_json_file(_overrides_path(), overrides)


def set_override(project_path: str, status: Status) -> None:
    """Set a manual status override for a project."""
    overrides = load_overrides()
    overrides[project_path] = status.value
    _save_overrides(overrides)


def get_override(project_path: str) -> Status | None:
    """Get the manual override for a project, or None if not set."""
    overrides = load_overrides()
    raw = overrides.get(project_path)
    if raw is None:
        return None
    try:
        return Status(raw)
    except ValueError:
        return None


def clear_override(project_path: str) -> None:
    """Remove the manual override — project returns to auto-computed status."""
    overrides = load_overrides()
    overrides.pop(project_path, None)
    _save_overrides(overrides)


def filter_archived(items: list) -> list:
    """Remove ARCHIVED projects from a list of Project-like or OverviewRow objects.

    Checks BOTH the override file AND cached status — so projects are
    filtered immediately after archiving, before the next scan.
    """
    overrides = load_overrides()

    def _is_archived(item: object) -> bool:
        # Check override first (instant, no scan needed)
        path_str = str(getattr(item, "path", ""))
        if overrides.get(path_str) == Status.ARCHIVED.value:
            return True
        # OverviewRow has status_raw (already override-aware from helpers.py)
        if hasattr(item, "status_raw"):
            return item.status_raw == Status.ARCHIVED.value
        # Project has metadata.status (cache value)
        if hasattr(item, "metadata") and item.metadata:
            return item.metadata.status == Status.ARCHIVED
        return False

    return [item for item in items if not _is_archived(item)]

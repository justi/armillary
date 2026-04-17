"""Manual status overrides — survive re-scans.

When a user runs `armillary archive <project>`, the override is stored
here. `compute_status()` checks overrides BEFORE applying heuristics,
so an ARCHIVED project stays ARCHIVED regardless of git activity.

Storage: JSON file next to the cache DB, same pattern as excluded.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from .cache import default_db_path
from .models import Status

_OVERRIDES_FILENAME = "status-overrides.json"


def _overrides_path() -> Path:
    return default_db_path().parent / _OVERRIDES_FILENAME


def load_overrides() -> dict[str, str]:
    """Load {project_path: status_string} from disk."""
    path = _overrides_path()
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            return {k: v for k, v in parsed.items() if isinstance(v, str)}
    except (ValueError, OSError):
        pass
    return {}


def _save_overrides(overrides: dict[str, str]) -> None:
    path = _overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(overrides, indent=2, ensure_ascii=False), encoding="utf-8"
    )


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
    """Remove ARCHIVED projects from a list of Project-like or OverviewRow objects."""

    def _is_archived(item: object) -> bool:
        # OverviewRow has status_raw
        if hasattr(item, "status_raw"):
            return item.status_raw == Status.ARCHIVED.value
        # Project has metadata.status
        if hasattr(item, "metadata") and item.metadata:
            return item.metadata.status == Status.ARCHIVED
        return False

    return [item for item in items if not _is_archived(item)]

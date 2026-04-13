"""Hard exclude for projects that shouldn't appear in any output.

Excluded projects (forks, tutorials, other people's tools) are filtered
from: next, context, search, overview, bridge, MCP tools.

Storage: JSON file next to the cache DB. Survives re-scans.
"""

from __future__ import annotations

import json
from pathlib import Path

from .cache import default_db_path

_EXCLUDES_FILENAME = "excluded.json"


def _excludes_path() -> Path:
    return default_db_path().parent / _EXCLUDES_FILENAME


def load_excluded() -> set[str]:
    """Load set of excluded project paths from disk."""
    path = _excludes_path()
    if not path.exists():
        return set()
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, list):
            return {str(p) for p in parsed}
    except (ValueError, OSError):
        pass
    return set()


def save_excluded(paths: set[str]) -> None:
    """Persist excluded paths to disk."""
    path = _excludes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sorted(paths), indent=2),
        encoding="utf-8",
    )


def exclude_project(project_path: str) -> None:
    """Add a project to the exclude list."""
    excluded = load_excluded()
    excluded.add(project_path)
    save_excluded(excluded)


def include_project(project_path: str) -> None:
    """Remove a project from the exclude list."""
    excluded = load_excluded()
    excluded.discard(project_path)
    save_excluded(excluded)


def is_excluded(project_path: str) -> bool:
    """Check if a project is excluded."""
    return project_path in load_excluded()


def filter_excluded(items: list, *, path_attr: str = "path") -> list:
    """Filter out excluded items from a list of objects with a path attribute."""
    excluded = load_excluded()
    if not excluded:
        return items
    return [item for item in items if str(getattr(item, path_attr)) not in excluded]

"""Project purpose: one-sentence description of why the project exists.

User-editable field stored in JSON next to the cache DB.
Survives re-scans. Keyed by project path.
"""

from __future__ import annotations

import json
from pathlib import Path

from .cache import default_db_path

_PURPOSE_FILENAME = "purposes.json"


def _purpose_path() -> Path:
    return default_db_path().parent / _PURPOSE_FILENAME


def load_purposes() -> dict[str, str]:
    """Load {project_path: purpose_string} from disk."""
    path = _purpose_path()
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            return {k: v for k, v in parsed.items() if isinstance(v, str)}
    except (ValueError, OSError):
        pass
    return {}


def _save_purposes(purposes: dict[str, str]) -> None:
    path = _purpose_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(purposes, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def set_purpose(project_path: str, purpose: str) -> None:
    """Set or update the purpose for a project."""
    purposes = load_purposes()
    purposes[project_path] = purpose.strip()
    _save_purposes(purposes)


def get_purpose(project_path: str) -> str | None:
    """Get the purpose for a project, or None if not set."""
    return load_purposes().get(project_path)


def clear_purpose(project_path: str) -> None:
    """Remove the purpose for a project."""
    purposes = load_purposes()
    purposes.pop(project_path, None)
    _save_purposes(purposes)


# --- Archive reasons (ADR 0019) ---

_REASONS_FILENAME = "archive-reasons.json"


def _reasons_path() -> Path:
    return default_db_path().parent / _REASONS_FILENAME


def load_archive_reasons() -> dict[str, str]:
    """Load {project_path: reason} from disk."""
    path = _reasons_path()
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            return {k: v for k, v in parsed.items() if isinstance(v, str)}
    except (ValueError, OSError):
        pass
    return {}


def set_archive_reason(project_path: str, reason: str) -> None:
    """Record why a project was archived."""
    reasons = load_archive_reasons()
    reasons[project_path] = reason.strip()
    path = _reasons_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reasons, indent=2, ensure_ascii=False), encoding="utf-8")


def get_archive_reason(project_path: str) -> str | None:
    """Get the archive reason for a project."""
    return load_archive_reasons().get(project_path)

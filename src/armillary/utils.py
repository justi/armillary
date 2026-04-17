"""Shared utility helpers used by both CLI and UI layers."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from armillary.config import Config, ConfigError, load_config
from armillary.models import Project

_T = TypeVar("_T")


def shorten_home(path: Path) -> str:
    """Replace the user's home directory prefix with ``~``."""
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home) :] if s.startswith(home) else s


def safe_load_config() -> Config | None:
    """Load the config file, returning *None* on any :class:`ConfigError`.

    Both CLI and UI call this when they need the config but can degrade
    gracefully if it is missing or broken.
    """
    try:
        return load_config()
    except ConfigError:
        return None


def load_json_str_dict(path: Path) -> dict[str, str]:
    """Load a JSON object containing only string values."""
    return _load_json(path, default={}, coerce=_coerce_str_dict)


def load_json_number_dict(path: Path) -> dict[str, int | float]:
    """Load a JSON object containing only numeric values."""
    return _load_json(path, default={}, coerce=_coerce_number_dict)


def load_json_str_list(path: Path) -> list[str]:
    """Load a JSON array containing only string values."""
    return _load_json(path, default=[], coerce=_coerce_str_list)


def read_json_file(path: Path) -> object | None:
    """Read a JSON file, returning *None* when it is missing or invalid."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def write_json_file(
    path: Path,
    payload: object,
    *,
    ensure_ascii: bool = False,
) -> None:
    """Write JSON to disk with the project's standard formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=ensure_ascii),
        encoding="utf-8",
    )


def resolve_project_by_name(
    projects: list[Project],
    project_name: str,
) -> Project | None:
    """Resolve a project by substring, preferring a single exact match."""
    matches = find_projects_by_name(projects, project_name)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    needle = project_name.lower()
    exact = [project for project in matches if project.name.lower() == needle]
    if len(exact) == 1:
        return exact[0]

    raise ValueError(summarize_project_matches(matches))


def find_projects_by_name(projects: list[Project], project_name: str) -> list[Project]:
    """Return all substring matches for a project name query."""
    needle = project_name.lower()
    return [project for project in projects if needle in project.name.lower()]


def summarize_project_matches(projects: list[Project], *, limit: int = 5) -> str:
    """Return a short human-readable summary of matching project names."""
    names = ", ".join(project.name for project in projects[:limit])
    suffix = "" if len(projects) <= limit else f" (+{len(projects) - limit} more)"
    return f"{names}{suffix}"


def _load_json(
    path: Path,
    *,
    default: _T,
    coerce: Callable[[object], _T | None],
) -> _T:
    parsed = read_json_file(path)
    if parsed is None:
        return default
    coerced = coerce(parsed)
    return default if coerced is None else coerced


def _coerce_str_dict(parsed: object) -> dict[str, str] | None:
    if not isinstance(parsed, dict):
        return None
    return {key: value for key, value in parsed.items() if isinstance(value, str)}


def _coerce_number_dict(parsed: object) -> dict[str, int | float] | None:
    if not isinstance(parsed, dict):
        return None
    return {
        key: value for key, value in parsed.items() if isinstance(value, (int, float))
    }


def _coerce_str_list(parsed: object) -> list[str] | None:
    if not isinstance(parsed, list):
        return None
    return [value for value in parsed if isinstance(value, str)]

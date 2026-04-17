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


# --- Last conversation date (ADR 0022 M1) ---

_CONVERSATIONS_FILENAME = "last-conversations.json"


def _conversations_path() -> Path:
    return default_db_path().parent / _CONVERSATIONS_FILENAME


def load_conversations() -> dict[str, str]:
    """Load {project_path: ISO date string} from disk."""
    path = _conversations_path()
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            return {k: v for k, v in parsed.items() if isinstance(v, str)}
    except (ValueError, OSError):
        pass
    return {}


def set_last_conversation(project_path: str, date_str: str) -> None:
    """Record when you last talked to a user about this project."""
    convos = load_conversations()
    convos[project_path] = date_str.strip()
    path = _conversations_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(convos, indent=2, ensure_ascii=False), encoding="utf-8")


def get_last_conversation(project_path: str) -> str | None:
    """Get the last conversation date for a project."""
    return load_conversations().get(project_path)


# --- Revenue/MRR (ADR 0022 M2) ---

_REVENUE_FILENAME = "revenue.json"


def _revenue_path() -> Path:
    return default_db_path().parent / _REVENUE_FILENAME


def load_revenue() -> dict[str, int]:
    """Load {project_path: monthly_revenue_usd} from disk."""
    path = _revenue_path()
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            return {k: v for k, v in parsed.items() if isinstance(v, (int, float))}
    except (ValueError, OSError):
        pass
    return {}


def set_revenue(project_path: str, amount: int) -> None:
    """Set monthly revenue for a project (USD)."""
    rev = load_revenue()
    rev[project_path] = amount
    path = _revenue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rev, indent=2, ensure_ascii=False), encoding="utf-8")


def get_revenue(project_path: str) -> int | None:
    """Get monthly revenue for a project, or None if not set."""
    return load_revenue().get(project_path)

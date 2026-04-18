"""Status transition detection + decision journal.

Detects when projects cross status boundaries (ACTIVE→DORMANT etc.)
and stores optional reasons ("why did this change?").

Storage: transition-journal.json + status-previous.json next to cache.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .cache import Cache, default_db_path
from .exclude_service import filter_excluded

_PREVIOUS_FILENAME = "status-previous.json"
_JOURNAL_FILENAME = "transition-journal.json"
_PENDING_FILENAME = "transitions-pending.json"

_MAX_DISPLAY = 5


def _previous_path(db_path: Path | None = None) -> Path:
    base = db_path.parent if db_path else default_db_path().parent
    return base / _PREVIOUS_FILENAME


def _journal_path(db_path: Path | None = None) -> Path:
    base = db_path.parent if db_path else default_db_path().parent
    return base / _JOURNAL_FILENAME


def _load_json(path: Path) -> object:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# --- Transition detection ---


def detect_and_store_transitions(
    *,
    db_path: Path | None = None,
) -> list[dict]:
    """Compare current status vs previous, store pending transitions.

    Called by scan_service after scan. Writes transitions to
    transitions-pending.json for CLI/dashboard to consume later,
    and records them in the decision journal.

    Returns the list of new transitions.
    """
    with Cache(db_path=db_path) as cache:
        projects = cache.list_projects()
    projects = filter_excluded(projects)
    # Do NOT filter_archived — we want to detect archive transitions too
    from .status_override import get_override

    previous = _load_json(_previous_path(db_path))
    if not isinstance(previous, dict):
        previous = {}

    current: dict[str, str] = {}
    transitions: list[dict] = []

    for p in projects:
        md = p.metadata
        if not md or not md.status:
            continue
        path_str = str(p.path)
        # Use override if present, else cached status
        override = get_override(path_str)
        current_status = override.value if override else md.status.value
        current[path_str] = current_status

        prev_status = previous.get(path_str)
        if prev_status and prev_status != current_status:
            transitions.append(
                {
                    "project": p.name,
                    "path": path_str,
                    "from_status": prev_status,
                    "to_status": current_status,
                    "date": datetime.now().isoformat()[:10],
                }
            )

    # Save current as previous for next comparison
    _save_json(_previous_path(db_path), current)

    if transitions:
        # Store as pending for CLI/dashboard to read
        _save_json(_pending_path(db_path), transitions)
        # Record in journal
        for t in transitions:
            record_journal_entry(
                t["path"],
                t["from_status"],
                t["to_status"],
                db_path=db_path,
            )

    return transitions


def consume_pending_transitions(
    *,
    db_path: Path | None = None,
) -> list[dict]:
    """Read and clear pending transitions. Called by CLI/dashboard."""
    path = _pending_path(db_path)
    data = _load_json(path)
    if not isinstance(data, list) or not data:
        return []
    # Clear pending after reading
    _save_json(path, [])
    return data


def _pending_path(db_path: Path | None = None) -> Path:
    base = db_path.parent if db_path else default_db_path().parent
    return base / _PENDING_FILENAME


# --- Decision journal ---


def load_journal(
    project_path: str,
    *,
    db_path: Path | None = None,
) -> list[dict]:
    """Load transition journal entries for a project."""
    data = _load_json(_journal_path(db_path))
    if not isinstance(data, dict):
        return []
    entries = data.get(project_path, [])
    return entries if isinstance(entries, list) else []


def record_journal_entry(
    project_path: str,
    from_status: str,
    to_status: str,
    reason: str | None = None,
    *,
    db_path: Path | None = None,
) -> None:
    """Record a status transition with optional reason."""
    path = _journal_path(db_path)
    data = _load_json(path)
    if not isinstance(data, dict):
        data = {}

    entries = data.get(project_path, [])
    if not isinstance(entries, list):
        entries = []

    entries.append(
        {
            "date": datetime.now().isoformat()[:10],
            "from": from_status,
            "to": to_status,
            "reason": reason,
        }
    )

    data[project_path] = entries
    _save_json(path, data)


def format_transitions(transitions: list[dict]) -> str:
    """Format transitions for CLI display."""
    if not transitions:
        return ""

    icons = {
        "DORMANT": "💤",
        "STALLED": "⚠️",
        "ACTIVE": "🔥",
        "ARCHIVED": "📦",
    }

    lines: list[str] = []
    for t in transitions[:_MAX_DISPLAY]:
        icon = icons.get(t["to_status"], "•")
        lines.append(
            f"{icon} {t['project']} → {t['to_status']} (was {t['from_status']})"
        )
    if len(transitions) > _MAX_DISPLAY:
        lines.append(f"  +{len(transitions) - _MAX_DISPLAY} more")

    return "\n".join(lines)

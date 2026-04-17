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
from .status_override import filter_archived

_PREVIOUS_FILENAME = "status-previous.json"
_JOURNAL_FILENAME = "transition-journal.json"

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


def detect_transitions(
    *,
    db_path: Path | None = None,
) -> list[dict]:
    """Compare current status vs previous, return list of transitions.

    Each transition: {project, path, from_status, to_status}
    Also updates status-previous.json for next comparison and records
    new transitions to the decision journal automatically.

    Idempotent across repeated calls within the same cache state:
    once current is saved as previous, subsequent calls return []
    until the cache changes again (i.e. after a scan).
    """
    with Cache(db_path=db_path) as cache:
        projects = cache.list_projects()
    projects = filter_excluded(projects)
    projects = filter_archived(projects)

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
        current_status = md.status.value
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

    # Auto-record new transitions to the decision journal so the
    # detail view's "Status transitions" expander has content.
    for t in transitions:
        record_journal_entry(
            t["path"],
            t["from_status"],
            t["to_status"],
            db_path=db_path,
        )

    return transitions


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

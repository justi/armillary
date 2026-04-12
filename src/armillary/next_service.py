"""Recommendation engine for `armillary next`.

Three categories of suggestions:
- **Momentum** — ACTIVE + recent commits → "continue"
- **Zombie** — ACTIVE but no commit in >7 days → "kill or ship"
- **Forgotten gold** — DORMANT/PAUSED with >50h invested → "finish with AI or archive"

Skip mechanism: projects can be dismissed for 30 days.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .cache import Cache, default_db_path
from .models import Project, Status

_SKIP_DURATION_DAYS = 30
_ZOMBIE_THRESHOLD_DAYS = 7
_GOLD_MIN_HOURS = 50
_MAX_SUGGESTIONS = 3


@dataclass(frozen=True)
class Suggestion:
    """A single recommendation from `armillary next`."""

    project: Project
    category: str  # "momentum", "zombie", "forgotten_gold"
    reason: str
    score: float


def get_suggestions(
    *,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> list[Suggestion]:
    """Return up to 3 suggestions from the cache, excluding skipped projects."""
    now = now or datetime.now()

    with Cache(db_path=db_path) as cache:
        projects = cache.list_projects()

    skips = _load_skips()
    active_skips = {
        path
        for path, ts in skips.items()
        if now.timestamp() - ts < _SKIP_DURATION_DAYS * 86400
    }

    candidates: list[Suggestion] = []

    for p in projects:
        if str(p.path) in active_skips:
            continue
        md = p.metadata
        if md is None or md.status is None:
            continue

        suggestion = _evaluate(p, now)
        if suggestion is not None:
            candidates.append(suggestion)

    candidates.sort(key=lambda s: s.score, reverse=True)

    # Pick at most one per category, up to MAX_SUGGESTIONS
    seen_categories: set[str] = set()
    result: list[Suggestion] = []
    for s in candidates:
        if s.category not in seen_categories:
            result.append(s)
            seen_categories.add(s.category)
        if len(result) >= _MAX_SUGGESTIONS:
            break

    # If we haven't filled 3 yet, add more from any category
    if len(result) < _MAX_SUGGESTIONS:
        for s in candidates:
            if s not in result:
                result.append(s)
            if len(result) >= _MAX_SUGGESTIONS:
                break

    return result


def skip_project(project_path: str) -> None:
    """Mark a project as skipped for 30 days."""
    skips = _load_skips()
    skips[project_path] = time.time()
    _save_skips(skips)


def _evaluate(project: Project, now: datetime) -> Suggestion | None:
    """Classify a project into momentum/zombie/forgotten_gold or None."""
    md = project.metadata
    if md is None or md.status is None:
        return None

    hours = md.work_hours or 0
    last_commit = md.last_commit_ts
    dirty = md.dirty_count or 0
    status = md.status

    if last_commit is not None:
        days_since = (now - last_commit).total_seconds() / 86400
    else:
        days_since = 999

    # Momentum: ACTIVE + committed recently
    if status == Status.ACTIVE and days_since <= _ZOMBIE_THRESHOLD_DAYS:
        score = hours * (1 / max(days_since, 0.1))
        if dirty > 0:
            reason = (
                f"{hours:.0f}h invested, {dirty} dirty file(s), "
                f"last commit {days_since:.0f}d ago — momentum is here."
            )
        else:
            reason = (
                f"{hours:.0f}h invested, "
                f"last commit {days_since:.0f}d ago — keep shipping."
            )
        return Suggestion(
            project=project, category="momentum", reason=reason, score=score
        )

    # Zombie: ACTIVE but stale
    if status == Status.ACTIVE and days_since > _ZOMBIE_THRESHOLD_DAYS:
        score = hours * 0.5
        reason = (
            f"{hours:.0f}h invested, no commit in {days_since:.0f} days — kill or ship?"
        )
        return Suggestion(
            project=project, category="zombie", reason=reason, score=score
        )

    # Forgotten gold: DORMANT/PAUSED with significant work
    if status in (Status.DORMANT, Status.PAUSED) and hours >= _GOLD_MIN_HOURS:
        months_since = days_since / 30
        score = hours * 0.3 * (1 / max(months_since, 0.1))
        reason = (
            f"{hours:.0f}h invested, abandoned {months_since:.0f} month(s) ago. "
            f"Finish with AI or archive?"
        )
        return Suggestion(
            project=project,
            category="forgotten_gold",
            reason=reason,
            score=score,
        )

    return None


# --- Skip persistence -------------------------------------------------------

_SKIPS_FILENAME = "next-skips.json"


def _skips_path() -> Path:
    """Path to the skips file — next to the cache DB."""
    return default_db_path().parent / _SKIPS_FILENAME


def _load_skips() -> dict[str, float]:
    """Load {project_path: timestamp} from disk."""
    path = _skips_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_skips(skips: dict[str, float]) -> None:
    """Persist skips to disk."""
    path = _skips_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(skips, indent=2), encoding="utf-8")

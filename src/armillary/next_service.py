"""Recommendation engine for `armillary next`.

Three categories of suggestions:
- **Momentum** — ACTIVE + recent commits → "continue"
- **Zombie** — ACTIVE but no commit in >7 days → "kill or ship"
- **Forgotten gold** — DORMANT/STALLED with >50h invested → "finish with AI or archive"

Skip mechanism: projects can be dismissed for 30 days.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .cache import Cache, default_db_path
from .exclude_service import filter_excluded
from .models import Project, Status
from .status_override import filter_archived
from .utils import read_json_file, write_json_file

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
    projects = filter_excluded(projects)
    projects = filter_archived(projects)

    # Record weekly pulse snapshot on every next call (idempotent per week)
    import contextlib

    with contextlib.suppress(Exception):
        from .pulse_service import take_snapshot

        take_snapshot(db_path=db_path)

    skips = _load_skips(db_path)
    active_skips = {
        path
        for path, entry in skips.items()
        if now.timestamp() - entry["timestamp"] < _SKIP_DURATION_DAYS * 86400
    }

    # Expired skips — projects returning after skip period
    expired_skips: dict[str, dict] = {
        path: entry
        for path, entry in skips.items()
        if now.timestamp() - entry["timestamp"] >= _SKIP_DURATION_DAYS * 86400
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
            # Enrich with skip history if this project was previously skipped
            skip_entry = expired_skips.get(str(p.path))
            if skip_entry:
                count = skip_entry.get("count", 1)
                last_reason = skip_entry.get("reason")
                skip_note = f" (skipped {count}x"
                if last_reason:
                    skip_note += f", last: {last_reason}"
                skip_note += ")"
                suggestion = Suggestion(
                    project=suggestion.project,
                    category=suggestion.category,
                    reason=suggestion.reason + skip_note,
                    score=suggestion.score,
                )
            candidates.append(suggestion)

    candidates.sort(key=lambda s: s.score, reverse=True)

    # Pick at most one per category, up to MAX_SUGGESTIONS.
    # Priority: momentum > zombie > forgotten_gold > archive_candidate
    _CATEGORY_PRIORITY = {
        "momentum": 0,
        "zombie": 1,
        "forgotten_gold": 2,
        "archive_candidate": 3,
    }
    candidates.sort(key=lambda s: (_CATEGORY_PRIORITY.get(s.category, 99), -s.score))

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


def skip_project(
    project_path: str,
    *,
    reason: str | None = None,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> None:
    """Mark a project as skipped for 30 days, with optional reason."""
    now = now or datetime.now()
    skips = _load_skips(db_path)
    prev = skips.get(project_path, {})
    prev_count = prev.get("count", 0) if isinstance(prev, dict) else 0
    skips[project_path] = {
        "timestamp": now.timestamp(),
        "reason": reason,
        "count": prev_count + 1,
    }
    _save_skips(skips, db_path)


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
        recency = "today" if days_since < 1 else f"{days_since:.0f}d ago"
        if dirty > 0:
            s = "s" if dirty > 1 else ""
            reason = (
                f"{hours:.0f}h invested, {dirty} uncommitted file{s}, "
                f"last commit {recency} — momentum is here."
            )
        else:
            reason = f"{hours:.0f}h invested, last commit {recency} — keep shipping."
        return Suggestion(
            project=project, category="momentum", reason=reason, score=score
        )

    # Zombie: ACTIVE but stale
    if status == Status.ACTIVE and days_since > _ZOMBIE_THRESHOLD_DAYS:
        score = hours * 0.5
        reason = (
            f"{hours:.0f}h invested, no commit in {days_since:.0f}d — kill or ship?"
        )
        return Suggestion(
            project=project, category="zombie", reason=reason, score=score
        )

    # Forgotten gold: DORMANT/STALLED with significant work
    if status in (Status.DORMANT, Status.STALLED) and hours >= _GOLD_MIN_HOURS:
        months_since = days_since / 30
        score = hours * 0.3 * (1 / max(months_since, 0.1))
        months_rounded = round(months_since)
        m_word = "month" if months_rounded == 1 else "months"
        reason = (
            f"{hours:.0f}h invested, abandoned {months_rounded} "
            f"{m_word} ago. Finish with AI or archive?"
        )
        return Suggestion(
            project=project,
            category="forgotten_gold",
            reason=reason,
            score=score,
        )

    # Archive candidate: low-effort project with dead velocity (ADR 0019)
    velocity = md.velocity_trend
    if (
        status in (Status.ACTIVE, Status.STALLED, Status.DORMANT)
        and velocity == "dead"
        and hours < _GOLD_MIN_HOURS
        and dirty == 0
    ):
        reason = (
            f"{hours:.0f}h invested, zero activity in 4 weeks, "
            f"no uncommitted work — archive this?"
        )
        return Suggestion(
            project=project,
            category="archive_candidate",
            reason=reason,
            score=hours * 0.1,
        )

    return None


# --- Skip persistence -------------------------------------------------------

_SKIPS_FILENAME = "next-skips.json"


def _skips_path(db_path: Path | None = None) -> Path:
    """Path to the skips file — next to the cache DB."""
    if db_path is not None:
        return db_path.parent / _SKIPS_FILENAME
    return default_db_path().parent / _SKIPS_FILENAME


def _load_skips(db_path: Path | None = None) -> dict[str, dict]:
    """Load skip data from disk.

    Handles both old format {path: timestamp} and new format
    {path: {timestamp, reason, count}}, migrating old entries on read.
    """
    path = _skips_path(db_path)
    parsed = read_json_file(path)
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, dict] = {}
    for k, v in parsed.items():
        if isinstance(v, (int, float)):
            # Migrate old format
            result[k] = {"timestamp": v, "reason": None, "count": 1}
        elif isinstance(v, dict) and "timestamp" in v:
            result[k] = v
    return result


def _save_skips(skips: dict[str, dict], db_path: Path | None = None) -> None:
    """Persist skips to disk."""
    write_json_file(_skips_path(db_path), skips)

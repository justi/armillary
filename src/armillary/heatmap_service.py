"""Activity heatmap — daily commit counts across all projects.

Generates data for a GitHub-contributions-style calendar heatmap.
Each day = total commits across ALL indexed projects.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from .cache import Cache
from .exclude_service import filter_excluded
from .status_override import filter_archived


def daily_activity(
    *,
    days: int = 365,
    db_path: Path | None = None,
) -> dict[date, int]:
    """Return {date: commit_count} for the last N days across all projects."""
    with Cache(db_path=db_path) as cache:
        projects = cache.list_projects()
    projects = filter_excluded(projects)
    projects = filter_archived(projects)

    counts: Counter[date] = Counter()
    since = (date.today() - timedelta(days=days)).isoformat()

    for p in projects:
        if p.type.value != "git":
            continue
        try:
            result = subprocess.run(
                [
                    "git",
                    "log",
                    "--format=%ad",
                    "--date=short",
                    f"--since={since}",
                ],
                cwd=str(p.path),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode != 0:
                continue
            for line in result.stdout.strip().splitlines():
                try:
                    counts[date.fromisoformat(line.strip())] += 1
                except ValueError:
                    continue
        except (OSError, subprocess.TimeoutExpired):
            continue

    return dict(counts)


def heatmap_summary(activity: dict[date, int]) -> dict[str, object]:
    """Compute summary stats from daily activity data."""
    if not activity:
        return {
            "total_commits": 0,
            "active_days": 0,
            "longest_streak": 0,
            "busiest_day": None,
            "busiest_count": 0,
        }

    total = sum(activity.values())
    active_days = len(activity)

    # Longest streak
    sorted_dates = sorted(activity.keys())
    streak = 1
    longest = 1
    for i in range(1, len(sorted_dates)):
        if sorted_dates[i] - sorted_dates[i - 1] == timedelta(days=1):
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 1

    busiest = max(activity, key=activity.get) if activity else None

    return {
        "total_commits": total,
        "active_days": active_days,
        "longest_streak": longest,
        "busiest_day": busiest,
        "busiest_count": activity.get(busiest, 0) if busiest else 0,
    }

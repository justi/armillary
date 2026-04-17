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


def export_heatmap_html(
    activity: dict[date, int],
    summary: dict[str, object],
) -> str:
    """Generate a standalone HTML card for sharing/screenshotting."""
    today = date.today()
    start = today - timedelta(days=364)
    peak = max(activity.values()) if activity else 1

    cells: list[str] = []
    for week in range(53):
        for day in range(7):
            d = start + timedelta(days=week * 7 + day)
            if d > today:
                cells.append('<div class="c" style="visibility:hidden"></div>')
                continue
            count = activity.get(d, 0)
            if count == 0:
                color = "#ebedf0"
            else:
                level = min(int(count / peak * 4), 3)
                color = ["#9be9a8", "#40c463", "#30a14e", "#216e39"][level]
            cells.append(f'<div class="c" style="background:{color}"></div>')

    total = summary.get("total_commits", 0)
    days_active = summary.get("active_days", 0)
    streak = summary.get("longest_streak", 0)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body{{margin:0;padding:24px;background:#0d1117;color:#c9d1d9;
font-family:-apple-system,BlinkMacSystemFont,sans-serif}}
h1{{font-size:18px;margin:0 0 4px}}
.sub{{color:#8b949e;font-size:13px;margin-bottom:16px}}
.grid{{display:grid;grid-template-rows:repeat(7,1fr);
grid-auto-flow:column;gap:2px;margin-bottom:16px}}
.c{{aspect-ratio:1;border-radius:2px;min-width:3px}}
.stats{{display:flex;gap:24px;font-size:14px}}
.stat b{{display:block;font-size:20px;color:#c9d1d9}}
.stat span{{color:#8b949e}}
</style></head><body>
<h1>armillary</h1>
<div class="sub">12 months of coding activity</div>
<div class="grid">{"".join(cells)}</div>
<div class="stats">
<div class="stat"><b>{total:,}</b><span>commits</span></div>
<div class="stat"><b>{days_active}</b><span>active days</span></div>
<div class="stat"><b>{streak}d</b><span>longest streak</span></div>
</div></body></html>"""

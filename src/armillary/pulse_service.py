"""Weekly pulse — what changed across your projects this week.

Not a report — a 5-line mirror. Shows:
- Projects you worked on (commits this week)
- Projects that went dormant (status decay)
- Uncommitted work aging (dirty files > 7 days)

Delivered via MCP (armillary_pulse) and CLI (armillary pulse).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .cache import Cache
from .exclude_service import filter_excluded
from .models import Status
from .status_override import filter_archived


@dataclass(frozen=True)
class PulseEntry:
    """One line in the weekly pulse."""

    icon: str
    project_name: str
    message: str


@dataclass(frozen=True)
class WeeklyPulse:
    """The full pulse — max ~10 lines."""

    worked_on: list[PulseEntry] = field(default_factory=list)
    went_dormant: list[PulseEntry] = field(default_factory=list)
    aging_wip: list[PulseEntry] = field(default_factory=list)
    period: str = "this week"


def generate_pulse(
    *,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> WeeklyPulse:
    """Generate the weekly pulse from cache data."""
    now = now or datetime.now()
    week_ago = now - timedelta(days=7)

    with Cache(db_path=db_path) as cache:
        projects = cache.list_projects()
    projects = filter_excluded(projects)
    projects = filter_archived(projects)

    worked_on: list[PulseEntry] = []
    went_dormant: list[PulseEntry] = []
    aging_wip: list[PulseEntry] = []

    for p in projects:
        md = p.metadata
        if md is None:
            continue

        # Projects with commits this week
        if md.last_commit_ts and md.last_commit_ts >= week_ago:
            hours = f"{md.work_hours:.0f}h" if md.work_hours else ""
            worked_on.append(
                PulseEntry(
                    icon="🔨",
                    project_name=p.name,
                    message=f"active this week · {hours}",
                )
            )

        # Projects that crossed into DORMANT (last commit 30-37 days ago)
        if (
            md.status == Status.DORMANT
            and md.last_commit_ts
            and timedelta(days=30) <= (now - md.last_commit_ts) < timedelta(days=37)
        ):
            went_dormant.append(
                PulseEntry(
                    icon="💤",
                    project_name=p.name,
                    message="went dormant this week",
                )
            )

        # Dirty files aging > 7 days
        if (
            md.status in (Status.STALLED, Status.ACTIVE)
            and md.dirty_count
            and md.dirty_count > 0
            and md.work_hours
            and md.work_hours > 10
        ):
            aging_wip.append(
                PulseEntry(
                    icon="⚠️",
                    project_name=p.name,
                    message=(
                        f"{md.dirty_count} uncommitted "
                        f"file{'s' if md.dirty_count > 1 else ''}"
                    ),
                )
            )

    # Sort by hours for worked_on
    worked_on.sort(key=lambda e: e.message, reverse=True)

    return WeeklyPulse(
        worked_on=worked_on[:5],
        went_dormant=went_dormant[:3],
        aging_wip=aging_wip[:5],
    )


def format_pulse(pulse: WeeklyPulse) -> str:
    """Format pulse as plain text for CLI/MCP."""
    if not pulse.worked_on and not pulse.went_dormant and not pulse.aging_wip:
        return "Quiet week — no project activity detected."

    lines: list[str] = []

    if pulse.worked_on:
        lines.append("Worked on:")
        for e in pulse.worked_on:
            lines.append(f"  {e.icon} {e.project_name} — {e.message}")

    if pulse.went_dormant:
        if lines:
            lines.append("")
        lines.append("Went dormant:")
        for e in pulse.went_dormant:
            lines.append(f"  {e.icon} {e.project_name} — {e.message}")

    if pulse.aging_wip:
        if lines:
            lines.append("")
        lines.append("Uncommitted work:")
        for e in pulse.aging_wip:
            lines.append(f"  {e.icon} {e.project_name} — {e.message}")

    return "\n".join(lines)

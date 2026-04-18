"""Distribution templates — tweet, Show HN, shareable copy.

Generates share-ready text from real portfolio data.
Never includes project names (privacy rule from panel).
"""

from __future__ import annotations

from pathlib import Path

from .cache import Cache
from .exclude_service import filter_excluded
from .status_override import filter_archived


def _portfolio_stats(*, db_path: Path | None = None) -> dict[str, object]:
    """Gather portfolio stats for templates."""
    from collections import Counter
    from datetime import datetime

    with Cache(db_path=db_path) as cache:
        projects = cache.list_projects()
    projects = filter_excluded(projects)
    projects = filter_archived(projects)

    from .models import ProjectType

    git_projects = [p for p in projects if p.type == ProjectType.GIT]
    total = len(projects)
    git_count = len(git_projects)
    total_hours = sum(p.metadata.work_hours or 0 for p in projects if p.metadata)
    statuses = Counter(
        p.metadata.status.value for p in projects if p.metadata and p.metadata.status
    )

    # Clamp to 2005 — git did not exist before; older timestamps are
    # git-svn/cvs imports with backdated author dates.
    _GIT_EPOCH = datetime(2005, 4, 1)
    first_dates = [
        p.metadata.first_commit_ts
        for p in projects
        if p.metadata
        and p.metadata.first_commit_ts
        and p.metadata.first_commit_ts >= _GIT_EPOCH
    ]
    if first_dates:
        oldest = min(first_dates)
        years = (datetime.now() - oldest).days / 365
        if years >= 2:
            span = f"{years:.0f} years"
        elif years >= 1:
            span = "over a year"
        else:
            months = (datetime.now() - oldest).days / 30
            span = f"{months:.0f} months"
    else:
        span = "a while"

    umbrellas = len({str(p.umbrella) for p in projects})

    # Projects active in last year
    year_ago = datetime.now() - __import__("datetime").timedelta(days=365)
    last_year_count = sum(
        1
        for p in projects
        if p.metadata
        and p.metadata.last_commit_ts
        and p.metadata.last_commit_ts >= year_ago
    )

    return {
        "total": total,
        "git_count": git_count,
        "total_hours": round(total_hours),
        "active": statuses.get("ACTIVE", 0),
        "dormant": statuses.get("DORMANT", 0),
        "stalled": statuses.get("STALLED", 0),
        "span": span,
        "last_year_count": last_year_count,
        "umbrellas": umbrellas,
    }


def generate_tweet(*, db_path: Path | None = None) -> str:
    """Generate a tweet template from portfolio data."""
    s = _portfolio_stats(db_path=db_path)
    if s["total"] < 5:
        return "Scan more projects first (need at least 5)."
    return (
        f"1st project {s['span']} ago. "
        f"{s['total_hours']:,}h of work across {s['total']} projects.\n"
        f"{s['last_year_count']} touched last year. "
        f"{s['active']} active right now.\n\n"
        f"armillary — total recall for everything you've ever built."
    )


def generate_hn_post(*, db_path: Path | None = None) -> str:
    """Generate a Show HN post body."""
    s = _portfolio_stats(db_path=db_path)
    if s["total"] < 5:
        return "Scan more projects first (need at least 5)."
    return (
        f"Show HN: armillary — never forget a side project again\n\n"
        f"I have {s['git_count']} git repos across {s['umbrellas']} folders.\n"
        f"{s['dormant']} are dormant ({s['total_hours']:,}h invested, "
        f"forgotten).\n"
        f"{s['active']} are active right now.\n\n"
        f"armillary indexes them, computes status from git signals,\n"
        f"and tells me what to work on each morning.\n\n"
        f"Built with: Python, GitPython, Streamlit, MCP"
    )

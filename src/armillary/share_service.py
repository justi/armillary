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

    total = len(projects)
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

    return {
        "total": total,
        "total_hours": round(total_hours),
        "active": statuses.get("ACTIVE", 0),
        "dormant": statuses.get("DORMANT", 0),
        "stalled": statuses.get("STALLED", 0),
        "span": span,
        "umbrellas": umbrellas,
    }


def generate_tweet(*, db_path: Path | None = None) -> str:
    """Generate a tweet template from portfolio data."""
    s = _portfolio_stats(db_path=db_path)
    if s["total"] < 5:
        return "Scan more projects first (need at least 5)."
    return (
        f"I built {s['total']} projects in {s['span']}.\n"
        f"{s['active']} active, {s['dormant']} forgotten, "
        f"{s['total_hours']:,}h total.\n\n"
        f"Tracking with armillary — total recall for side projects."
    )


def generate_hn_post(*, db_path: Path | None = None) -> str:
    """Generate a Show HN post body."""
    s = _portfolio_stats(db_path=db_path)
    if s["total"] < 5:
        return "Scan more projects first (need at least 5)."
    return (
        f"Show HN: armillary — never forget a side project again\n\n"
        f"I have {s['total']} git repos across {s['umbrellas']} folders.\n"
        f"{s['dormant']} are dormant ({s['total_hours']:,}h invested, "
        f"forgotten).\n"
        f"{s['active']} are active right now.\n\n"
        f"armillary indexes them, computes status from git signals,\n"
        f"and tells me what to work on each morning.\n\n"
        f"Built with: Python, GitPython, Streamlit, MCP"
    )

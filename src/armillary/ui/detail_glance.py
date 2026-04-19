"""At-a-glance 5-metric strip for the project detail page.

Split out of ``detail.py`` for the 400-line architecture target. Pure
rendering; picks Revenue / Commits as the fourth cell depending on
whether revenue is set.
"""

from __future__ import annotations

import streamlit as st

from armillary.models import Project
from armillary.ui.style import glance_strip


def _render_glance_strip(project: Project) -> None:
    """5-cell 'at a glance' metric strip (Last commit / Uncommitted /
    Invested / Revenue|Commits / Activity). Falls back gracefully when
    fields are missing."""
    from datetime import datetime

    md = project.metadata

    # Last commit
    if md and md.last_commit_ts:
        days = (datetime.now() - md.last_commit_ts).days
        if days == 0:
            last_val, last_sub = "today", "last commit"
        elif days == 1:
            last_val, last_sub = "1d", "ago"
        elif days < 30:
            last_val, last_sub = f"{days}d", "ago"
        elif days < 365:
            last_val, last_sub = f"{days // 30}mo", "ago"
        else:
            last_val, last_sub = f"{days // 365}y", "ago"
        # 90+ days is the stricter threshold — check it first or the >30
        # branch shortcircuits and the danger tone is unreachable.
        last_tone = "danger" if days > 90 else ("warning" if days > 30 else None)
    else:
        last_val, last_sub, last_tone = "\u2014", "no commits", None

    # Uncommitted
    dirty = (md.dirty_count if md else None) or 0
    dirty_tone = "warning" if dirty > 0 else None
    dirty_val = str(dirty)
    dirty_sub = "files" if dirty != 1 else "file"

    # Invested
    hours = md.work_hours if md else None
    invested_val = f"{hours:.0f}h" if hours else "\u2014"
    invested_sub = ""
    if md and md.first_commit_ts and md.last_commit_ts:
        span_days = max((md.last_commit_ts - md.first_commit_ts).days, 1)
        if span_days >= 30:
            invested_sub = f"over {span_days // 30}mo"

    # Revenue (killer metric) with Commits as fallback — panel feedback
    from armillary.purpose_service import get_revenue

    revenue = get_revenue(str(project.path))
    if revenue is not None and revenue > 0:
        fourth_label = "Revenue"
        fourth_val = f"${revenue}"
        fourth_sub = "monthly"
        fourth_tone = None
    else:
        fourth_label = "Commits"
        fourth_val = str(md.commit_count) if md and md.commit_count else "\u2014"
        fourth_sub = "total"
        fourth_tone = None

    # Activity sparkline (6mo)
    activity_val: str = "\u2014"
    activity_sub = "6 months"
    if md and md.monthly_commits and any(c > 0 for c in md.monthly_commits):
        from armillary.ui.style import sparkline_html

        activity_val = sparkline_html(
            md.monthly_commits,
            trend=getattr(md, "velocity_trend", None),
        )

    cells = [
        {
            "label": "Last commit",
            "value": last_val,
            "sub": last_sub,
            "tone": last_tone,
        },
        {
            "label": "Uncommitted",
            "value": dirty_val,
            "sub": dirty_sub,
            "tone": dirty_tone,
        },
        {
            "label": "Invested",
            "value": invested_val,
            "sub": invested_sub,
        },
        {
            "label": fourth_label,
            "value": fourth_val,
            "sub": fourth_sub,
            "tone": fourth_tone,
        },
        {
            "label": "Activity",
            "value": activity_val,
            "sub": activity_sub,
            "is_spark": True,
        },
    ]
    st.markdown(glance_strip(cells), unsafe_allow_html=True)

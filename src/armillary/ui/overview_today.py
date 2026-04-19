"""Today tab: projects touched in the last 24h.

Split out of ``overview.py``. ``find_today_activity`` is pure so tests
can exercise it without spinning up Streamlit; ``_render_today_activity``
is the view helper called from the tab container.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from armillary.ui.helpers import _STATUS_EMOJI, OverviewRow


def find_today_activity(
    rows: list[OverviewRow],
    *,
    now: datetime | None = None,
) -> list[OverviewRow]:
    """Projects touched in the last 24h (commit or any filesystem mtime).

    Pure function — testable without Streamlit. Uses ``last_modified``
    which the scan stores as the max of commit timestamp + head mtime,
    so a dirty edit without a commit still counts.
    """
    from datetime import datetime as _dt
    from datetime import timedelta

    ref = now or _dt.now()
    cutoff = ref - timedelta(hours=24)
    touched = [r for r in rows if r.last_modified and r.last_modified >= cutoff]
    touched.sort(key=lambda r: r.last_modified, reverse=True)
    return touched


def _render_today_activity(rows: list[OverviewRow]) -> None:
    """Today tab content: projects modified in the last 24h."""
    touched = find_today_activity(rows)

    if not touched:
        st.caption(
            "Nothing changed in the last 24 hours. "
            "Switch to Portfolio for history, search, and the heatmap."
        )
        return

    dirty_count = sum(1 for r in touched if r.dirty and r.dirty > 0)
    sub = f"{len(touched)} project{'s' if len(touched) > 1 else ''}"
    if dirty_count:
        sub += f" \u00b7 {dirty_count} with uncommitted work"
    st.caption(sub)

    for r in touched:
        delta = datetime.now() - r.last_modified
        minutes = int(delta.total_seconds() / 60)
        if minutes < 60:
            when = "just now" if minutes < 1 else f"{minutes}m ago"
        else:
            when = f"{minutes // 60}h ago"

        dirty_note = ""
        if r.dirty and r.dirty > 0:
            s = "s" if r.dirty > 1 else ""
            dirty_note = f" \u00b7 **{r.dirty} uncommitted file{s}**"

        emoji = _STATUS_EMOJI.get(r.status_raw, "\u00b7")
        col_info, col_open = st.columns([6, 1])
        with col_info:
            st.markdown(f"{emoji} **{r.name}** \u2014 {when}{dirty_note}")
        with col_open:
            if st.button(
                "Open",
                key=f"today_open_{r.path}",
                icon=":material/open_in_new:",
            ):
                st.query_params["project"] = r.path
                st.rerun()

"""Time-grouped project tables + the pure ``group_by_time`` helper.

Split out of ``overview.py`` for the 400-line target. The dataframe
widget + multi-select + bulk-archive action all live here.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from armillary.ui.helpers import _STATUS_EMOJI, OverviewRow


def group_by_time(
    rows: list[OverviewRow],
) -> dict[str, list[OverviewRow]]:
    """Split rows into 'last_month', 'last_year', 'older'.

    Pure function — testable without Streamlit.
    """
    from datetime import datetime as _dt
    from datetime import timedelta

    now = _dt.now()
    month_ago = now - timedelta(days=30)
    year_ago = now - timedelta(days=365)
    return {
        "last_month": [r for r in rows if r.last_modified >= month_ago],
        "last_year": [r for r in rows if month_ago > r.last_modified >= year_ago],
        "older": [r for r in rows if r.last_modified < year_ago],
    }


def _render_time_grouped_tables(rows: list[OverviewRow]) -> None:
    """Show projects in time groups: last month, last year, older.

    Only non-empty groups are rendered. Each gets a subheader + table.
    Uses the pure ``group_by_time`` helper so both prod and tests share
    the same bucketing logic.
    """
    groups = group_by_time(rows)
    this_month = groups["last_month"]
    this_year = groups["last_year"]
    older = groups["older"]

    if this_month:
        st.caption(f"Last month \u2014 {len(this_month)} projects")
        _render_table(this_month, key_prefix="month")
    if this_year:
        with st.expander(
            f"Last year \u2014 {len(this_year)} projects",
            expanded=False,
        ):
            _render_table(this_year, key_prefix="year")
    if older:
        with st.expander(
            f"Older \u2014 {len(older)} projects",
            expanded=False,
        ):
            _render_table(older, key_prefix="older")
    if not rows:
        st.warning("No projects in cache.")


def _render_table(rows: list[OverviewRow], *, key_prefix: str = "default") -> None:
    """Compact dataframe with multi-select + action bar.

    Streamlit widget keys must be unique across the page; when the
    overview renders multiple time-grouped tables in one rerun each
    instance needs its own ``bulk_archive`` key, otherwise the second
    instance raises a duplicate-widget error."""
    display = []
    for r in rows:
        emoji = _STATUS_EMOJI.get(r.status_raw, "·")

        parts = []
        if r.commits is not None:
            parts.append(f"{r.commits} commits")
        if r.branch and r.branch != "—":
            parts.append(r.branch)
        summary = ", ".join(parts) if parts else "—"

        if r.last_modified:
            delta = datetime.now() - r.last_modified
            days = delta.days
            if days == 0:
                last = "today"
            elif days == 1:
                last = "1d ago"
            elif days < 30:
                last = f"{days}d ago"
            elif days < 365:
                last = f"{days // 30}mo ago"
            else:
                last = f"{days // 365}y ago"
        else:
            last = "—"

        display.append(
            {
                "Name": f"{emoji} {r.name}",
                "Summary": summary,
                "Hours": r.work_hours or 0,
                "Last": last,
            }
        )

    event = st.dataframe(
        display,
        height=400,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        column_config={
            "Name": st.column_config.TextColumn("Name", pinned=True),
            "Summary": st.column_config.TextColumn("Summary"),
            "Hours": st.column_config.ProgressColumn(
                "Hours",
                min_value=0,
                max_value=max((r.work_hours or 0 for r in rows), default=100),
                format="%.0f",
                help="Estimated work hours (commit gap < 4h)",
            ),
            "Last": st.column_config.TextColumn("Last", width="small"),
        },
    )

    selection = getattr(event, "selection", None)
    selected_indices = getattr(selection, "rows", []) if selection else []

    if len(selected_indices) == 1:
        idx = selected_indices[0]
        if idx < len(rows):
            st.query_params["project"] = rows[idx].path
            st.rerun()
    elif len(selected_indices) > 1:
        col_info, col_action = st.columns([3, 1])
        with col_info:
            st.caption(f"{len(selected_indices)} projects selected")
        with col_action:
            if st.button(
                "Archive selected",
                key=f"bulk_archive_{key_prefix}",
                icon=":material/archive:",
                type="secondary",
            ):
                from armillary.models import Status
                from armillary.status_override import set_override

                for i in selected_indices:
                    if i < len(rows):
                        set_override(rows[i].path, Status.ARCHIVED)
                st.toast(f"Archived {len(selected_indices)} projects")
                st.rerun()

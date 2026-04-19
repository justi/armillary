"""Portfolio tab content: weekly pulse + 12-month activity heatmap.

Split out of ``overview.py`` for the 400-line target. Both sections
render inside expanders on the Portfolio tab (or inline when the
Today tab is skipped).
"""

from __future__ import annotations

import streamlit as st


def _render_pulse_section() -> None:
    """Weekly pulse — what changed this week (ADR 0018)."""
    from armillary.pulse_service import generate_pulse

    pulse = generate_pulse()
    if not pulse.worked_on and not pulse.went_dormant and not pulse.aging_wip:
        return

    total_entries = (
        len(pulse.worked_on) + len(pulse.went_dormant) + len(pulse.aging_wip)
    )
    with st.expander(
        f"Weekly pulse ({total_entries} updates)",
        icon=":material/monitoring:",
        expanded=False,
    ):
        if pulse.worked_on:
            st.markdown("**Worked on this week**")
            for e in pulse.worked_on:
                st.markdown(f"- {e.icon} **{e.project_name}** \u2014 {e.message}")
        if pulse.went_dormant:
            st.markdown("**Went dormant**")
            for e in pulse.went_dormant:
                st.markdown(f"- {e.icon} **{e.project_name}** \u2014 {e.message}")
        if pulse.aging_wip:
            st.markdown("**Uncommitted work**")
            for e in pulse.aging_wip:
                st.markdown(f"- {e.icon} **{e.project_name}** \u2014 {e.message}")

        from armillary.pulse_service import load_history

        history = load_history()
        if len(history) < 2:
            st.caption(
                f"Portfolio chart appears after 2 weeks ({len(history)}/2 recorded)"
            )
        if len(history) >= 2:
            import pandas as pd

            st.markdown("---")
            st.markdown("**Portfolio evolution**")
            df = pd.DataFrame(history)
            df["date"] = pd.to_datetime(df["date"])
            st.area_chart(
                df.set_index("date")[["active", "stalled", "dormant"]],
                color=["#40c463", "#f0ad4e", "#666666"],
            )


def _render_activity_heatmap() -> None:
    """GitHub-contributions-style heatmap (ADR 0020)."""
    with st.expander(
        "Activity heatmap (12 months)",
        icon=":material/calendar_month:",
        expanded=False,
    ):
        from armillary.heatmap_service import daily_activity, heatmap_summary

        activity = daily_activity()
        if not activity:
            st.caption("No commit activity in the last 12 months.")
            return

        summary = heatmap_summary(activity)

        cols = st.columns(4)
        with cols[0]:
            st.metric("Commits", f"{summary['total_commits']:,}")
        with cols[1]:
            st.metric("Active days", summary["active_days"])
        with cols[2]:
            st.metric("Longest streak", f"{summary['longest_streak']}d")
        with cols[3]:
            if summary["busiest_day"]:
                st.metric(
                    "Busiest day",
                    f"{summary['busiest_count']}",
                    help=str(summary["busiest_day"]),
                )

        from datetime import date, timedelta

        today = date.today()
        start = today - timedelta(days=364)

        peak = max(activity.values()) if activity else 1
        cells: list[str] = []
        for week in range(53):
            for day in range(7):
                d = start + timedelta(days=week * 7 + day)
                if d > today:
                    cells.append(
                        '<div class="hm-cell" style="visibility:hidden"></div>'
                    )
                    continue
                count = activity.get(d, 0)
                if count == 0:
                    color = "#ebedf0"
                else:
                    level = min(int(count / peak * 4), 3)
                    color = ["#9be9a8", "#40c463", "#30a14e", "#216e39"][level]
                tip = f"{d.isoformat()}: {count} commits"
                cells.append(
                    f'<div class="hm-cell" style="background:{color}" '
                    f'title="{tip}"></div>'
                )

        html = (
            "<style>"
            ".hm-grid{display:grid;"
            "grid-template-rows:repeat(7,1fr);"
            "grid-auto-flow:column;"
            "gap:2px;width:100%}"
            ".hm-cell{aspect-ratio:1;border-radius:2px;"
            "min-width:2px;min-height:2px}"
            "</style>"
            '<div class="hm-grid">' + "".join(cells) + "</div>"
        )
        st.html(html)

        from armillary.heatmap_service import export_heatmap_html

        card_html = export_heatmap_html(activity, summary)
        st.download_button(
            "Download heatmap (HTML)",
            data=card_html,
            file_name="armillary-card.html",
            mime="text/html",
            icon=":material/download:",
        )

"""Status signals: merged strip (zombies/at-risk/forgotten), dormant
banner, and the pure ``apply_status_filter`` / ``find_at_risk_projects``
functions. Split out of ``overview.py`` for the 400-line target.
"""

from __future__ import annotations

import streamlit as st

from armillary.ui.helpers import OverviewRow
from armillary.ui.style import (
    ACCENT_DANGER,
    ACCENT_FORGOTTEN,
    ACCENT_WARNING,
    status_strip,
    status_strip_cell,
)


def apply_status_filter(
    rows: list[OverviewRow],
    selected: list[str],
) -> list[OverviewRow]:
    """Filter rows by status. No selection = ACTIVE + STALLED default.

    Pure function — testable without Streamlit.
    """
    if selected:
        return [r for r in rows if r.status_raw in selected]
    return [r for r in rows if r.status_raw in ("ACTIVE", "STALLED")]


def find_at_risk_projects(
    rows: list[OverviewRow],
    *,
    exclude_paths: set[str] | None = None,
) -> list[OverviewRow]:
    """STALLED + dirty + >10h = uncommitted work at risk.

    Pure function — testable without Streamlit.
    """
    skip = exclude_paths or set()
    return [
        r
        for r in rows
        if r.status_raw == "STALLED"
        and r.dirty
        and r.dirty > 0
        and r.work_hours
        and r.work_hours > 10
        and r.path not in skip
    ]


def _render_status_strip(
    rows: list[OverviewRow],
    *,
    exclude_paths: set[str] | None = None,
) -> None:
    """Merged 3-cell strip replacing separate zombie/at-risk/forgotten banners.

    Change #2 from the design handoff: one grid, three icons + numbers.
    """
    from datetime import datetime, timedelta

    cutoff = datetime.now() - timedelta(days=14)
    zombies = [
        r
        for r in rows
        if r.status_raw == "ACTIVE"
        and r.last_modified < cutoff
        and r.work_hours
        and r.work_hours > 10
    ]
    at_risk = find_at_risk_projects(rows, exclude_paths=exclude_paths)
    dormant = [r for r in rows if r.status_raw == "DORMANT"]

    if not zombies and not at_risk and not dormant:
        return

    dormant_hours = sum(r.work_hours or 0 for r in dormant)
    at_risk_hours = sum(r.work_hours or 0 for r in at_risk)

    cells = [
        status_strip_cell(
            icon="\u26a0\ufe0f",
            color=ACCENT_WARNING,
            count=len(zombies),
            label="zombies",
            sub="no commit 14+ days" if zombies else "none \u2014 nice",
            tooltip=(
                "ACTIVE projects with no commits in 14+ days. Decide: kill or ship?"
            ),
        ),
        status_strip_cell(
            icon="\U0001f4dd",
            color=ACCENT_DANGER,
            count=len(at_risk),
            label="at risk",
            sub=(f"{at_risk_hours:.0f}h uncommitted" if at_risk else "all committed"),
            tooltip="STALLED projects with uncommitted work. Push or lose hours.",
        ),
        status_strip_cell(
            icon="\U0001f4b0",
            color=ACCENT_FORGOTTEN,
            count=len(dormant),
            label="forgotten",
            sub=(f"{dormant_hours:.0f}h invested" if dormant else "none dormant"),
            tooltip="DORMANT projects — dev abandoned 30+ days. Revive or archive.",
        ),
    ]
    st.markdown(status_strip(cells), unsafe_allow_html=True)

    if at_risk:
        with st.expander(
            f"Show {len(at_risk)} at-risk project{'s' if len(at_risk) > 1 else ''}",
            expanded=False,
        ):
            for r in at_risk:
                hours_s = f"{r.work_hours:.0f}h" if r.work_hours else "0h"
                dirty_label = f"{r.dirty} file{'s' if r.dirty > 1 else ''}"
                col_info, col_act = st.columns([4, 1])
                with col_info:
                    st.markdown(
                        f"**{r.name}** \u2014 {dirty_label} uncommitted "
                        f"\u00b7 {hours_s}"
                    )
                with col_act:
                    if st.button(
                        "Open",
                        key=f"strip_atrisk_{r.path}",
                        icon=":material/open_in_new:",
                    ):
                        st.query_params["project"] = r.path
                        st.rerun()


def _render_dormant_banner(rows: list[OverviewRow], *, exploring: bool) -> None:
    """Golden banner for forgotten projects, or success bar when exploring."""
    dormant = [r for r in rows if r.status_raw == "DORMANT"]
    if not dormant:
        return

    total_hours = sum(r.work_hours or 0 for r in dormant)

    if exploring:
        col_msg, col_btn = st.columns([4, 1])
        with col_msg:
            st.success(
                f"Showing {len(dormant)} dormant projects · "
                f"{total_hours:.0f}h invested",
                icon=":material/savings:",
            )
        with col_btn:
            if st.button(
                "Clear filter",
                icon=":material/close:",
                key="clear_dormant_explore",
            ):
                st.session_state["_dormant_explore"] = False
                st.rerun()
    else:
        _ = total_hours  # kept for clarity; strip displays the aggregate
        if st.button(
            f"Explore {len(dormant)} forgotten projects",
            icon=":material/savings:",
            key="explore_dormant",
            type="secondary",
        ):
            st.session_state["_dormant_explore"] = True
            st.rerun()

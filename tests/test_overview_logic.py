"""Tests for overview page logic — filters and classifications.

These test the pure functions extracted from overview.py, NOT
the Streamlit rendering. Catches regressions like:
- Default filter hiding DORMANT from explore mode
- At-risk metric catching active projects (false zombie)
- ARCHIVED leaking into default table
"""

from __future__ import annotations

from datetime import datetime, timedelta

from armillary.ui.helpers import OverviewRow
from armillary.ui.overview import (
    apply_status_filter,
    find_at_risk_projects,
    group_by_time,
)


def _row(
    name: str,
    *,
    status: str = "ACTIVE",
    dirty: int | None = 0,
    work_hours: float | None = 100,
    path: str | None = None,
    last_modified: datetime | None = None,
) -> OverviewRow:
    return OverviewRow(
        status_label=f"🟢 {status}",
        status_raw=status,
        type="git",
        name=name,
        branch="main",
        dirty=dirty,
        commits=50,
        work_hours=work_hours,
        umbrella="~/Projects",
        last_modified=last_modified or datetime.now(),
        path=path or f"/tmp/{name}",
    )


# --- apply_status_filter ---


class TestApplyStatusFilter:
    def test_no_selection_shows_active_and_paused(self) -> None:
        rows = [
            _row("a", status="ACTIVE"),
            _row("b", status="PAUSED"),
            _row("c", status="DORMANT"),
            _row("d", status="IDEA"),
            _row("e", status="ARCHIVED"),
        ]
        filtered = apply_status_filter(rows, [])
        names = {r.name for r in filtered}
        assert names == {"a", "b"}

    def test_archived_hidden_by_default(self) -> None:
        rows = [_row("archived", status="ARCHIVED")]
        assert apply_status_filter(rows, []) == []

    def test_archived_visible_when_explicitly_selected(self) -> None:
        rows = [
            _row("a", status="ACTIVE"),
            _row("b", status="ARCHIVED"),
        ]
        filtered = apply_status_filter(rows, ["ARCHIVED"])
        assert len(filtered) == 1
        assert filtered[0].name == "b"

    def test_dormant_visible_when_selected(self) -> None:
        """Regression: dormant explore needs DORMANT visible."""
        rows = [
            _row("a", status="ACTIVE"),
            _row("b", status="DORMANT"),
        ]
        filtered = apply_status_filter(rows, ["DORMANT"])
        assert len(filtered) == 1
        assert filtered[0].name == "b"

    def test_multiple_statuses(self) -> None:
        rows = [
            _row("a", status="ACTIVE"),
            _row("b", status="DORMANT"),
            _row("c", status="IDEA"),
        ]
        filtered = apply_status_filter(rows, ["ACTIVE", "IDEA"])
        assert {r.name for r in filtered} == {"a", "c"}


# --- find_at_risk_projects ---


class TestFindAtRiskProjects:
    def test_paused_with_dirty_and_hours(self) -> None:
        rows = [_row("wip", status="PAUSED", dirty=3, work_hours=50)]
        assert len(find_at_risk_projects(rows)) == 1

    def test_active_never_at_risk(self) -> None:
        """Regression: ACTIVE projects should never be flagged."""
        rows = [_row("active", status="ACTIVE", dirty=0, work_hours=500)]
        assert find_at_risk_projects(rows) == []

    def test_dormant_never_at_risk(self) -> None:
        rows = [_row("dormant", status="DORMANT", dirty=5, work_hours=200)]
        assert find_at_risk_projects(rows) == []

    def test_low_hours_filtered_out(self) -> None:
        """Regression: blog with 0h, flow with 3h should not appear."""
        rows = [
            _row("blog", status="PAUSED", dirty=1, work_hours=0),
            _row("flow", status="PAUSED", dirty=2, work_hours=3),
        ]
        assert find_at_risk_projects(rows) == []

    def test_paused_clean_not_at_risk(self) -> None:
        rows = [_row("clean-pause", status="PAUSED", dirty=0, work_hours=100)]
        assert find_at_risk_projects(rows) == []

    def test_exclude_paths(self) -> None:
        rows = [_row("wip", status="PAUSED", dirty=2, work_hours=50)]
        assert find_at_risk_projects(rows, exclude_paths={"/tmp/wip"}) == []

    def test_archived_not_at_risk(self) -> None:
        rows = [_row("old", status="ARCHIVED", dirty=3, work_hours=200)]
        assert find_at_risk_projects(rows) == []


# --- dormant explore integration ---


class TestDormantExplore:
    """Regression: default ACTIVE+PAUSED filter must not kill dormant explore."""

    def test_dormant_explore_bypasses_default_filter(self) -> None:
        rows = [
            _row("a", status="ACTIVE"),
            _row("b", status="DORMANT", work_hours=200),
            _row("c", status="DORMANT", work_hours=50),
        ]
        # Simulate dormant explore: filter DORMANT directly, skip _apply_filters
        dormant = [r for r in rows if r.status_raw == "DORMANT"]
        dormant.sort(key=lambda r: r.work_hours or 0, reverse=True)
        assert len(dormant) == 2
        assert dormant[0].name == "b"  # sorted by hours desc

    def test_default_filter_does_not_include_dormant(self) -> None:
        rows = [
            _row("a", status="ACTIVE"),
            _row("b", status="DORMANT"),
        ]
        filtered = apply_status_filter(rows, [])
        assert all(r.status_raw != "DORMANT" for r in filtered)


# --- time-based grouping ---


class TestGroupByTime:
    def test_groups_by_recency(self) -> None:
        now = datetime.now()
        rows = [
            _row("recent", last_modified=now - timedelta(days=5)),
            _row("mid", last_modified=now - timedelta(days=100)),
            _row("old", last_modified=now - timedelta(days=500)),
        ]
        groups = group_by_time(rows)
        assert [r.name for r in groups["last_month"]] == ["recent"]
        assert [r.name for r in groups["last_year"]] == ["mid"]
        assert [r.name for r in groups["older"]] == ["old"]

    def test_empty_groups_have_empty_lists(self) -> None:
        now = datetime.now()
        rows = [_row("recent", last_modified=now - timedelta(days=1))]
        groups = group_by_time(rows)
        assert len(groups["last_month"]) == 1
        assert groups["last_year"] == []
        assert groups["older"] == []

    def test_all_projects_accounted_for(self) -> None:
        now = datetime.now()
        rows = [
            _row(f"p{i}", last_modified=now - timedelta(days=i * 50)) for i in range(10)
        ]
        groups = group_by_time(rows)
        total = sum(len(v) for v in groups.values())
        assert total == len(rows)

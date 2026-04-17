"""Tests for v2 features (M1-M3): last conversation, revenue, pulse
history, bulk archive logic, zombie alert, shareable card.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from armillary.models import ProjectMetadata, Status

# --- fixtures ---------------------------------------------------------------


@pytest.fixture()
def _use_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "cache.db"
    monkeypatch.setenv("ARMILLARY_CACHE_DB", str(db_path))
    return db_path


# --- last conversation ------------------------------------------------------


class TestPurpose:
    def test_set_get_and_clear(self, _use_tmp: Path) -> None:
        from armillary.purpose_service import (
            clear_purpose,
            get_purpose,
            set_purpose,
        )

        set_purpose("/tmp/proj", "Ship the billing rewrite.")
        assert get_purpose("/tmp/proj") == "Ship the billing rewrite."

        clear_purpose("/tmp/proj")
        assert get_purpose("/tmp/proj") is None


class TestLastConversation:
    def test_set_and_get(self, _use_tmp: Path) -> None:
        from armillary.purpose_service import (
            get_last_conversation,
            set_last_conversation,
        )

        set_last_conversation("/tmp/proj", "2026-04-15")
        assert get_last_conversation("/tmp/proj") == "2026-04-15"

    def test_get_returns_none_when_not_set(self, _use_tmp: Path) -> None:
        from armillary.purpose_service import get_last_conversation

        assert get_last_conversation("/tmp/nonexistent") is None

    def test_overwrite(self, _use_tmp: Path) -> None:
        from armillary.purpose_service import (
            get_last_conversation,
            set_last_conversation,
        )

        set_last_conversation("/tmp/proj", "2026-04-10")
        set_last_conversation("/tmp/proj", "2026-04-15")
        assert get_last_conversation("/tmp/proj") == "2026-04-15"


# --- revenue ----------------------------------------------------------------


class TestRevenue:
    def test_set_and_get(self, _use_tmp: Path) -> None:
        from armillary.purpose_service import get_revenue, set_revenue

        set_revenue("/tmp/proj", 500)
        assert get_revenue("/tmp/proj") == 500

    def test_get_returns_none_when_not_set(self, _use_tmp: Path) -> None:
        from armillary.purpose_service import get_revenue

        assert get_revenue("/tmp/nonexistent") is None

    def test_zero_revenue(self, _use_tmp: Path) -> None:
        from armillary.purpose_service import get_revenue, set_revenue

        set_revenue("/tmp/proj", 0)
        assert get_revenue("/tmp/proj") == 0

    def test_overwrite(self, _use_tmp: Path) -> None:
        from armillary.purpose_service import get_revenue, set_revenue

        set_revenue("/tmp/proj", 100)
        set_revenue("/tmp/proj", 200)
        assert get_revenue("/tmp/proj") == 200


# --- pulse history ----------------------------------------------------------


class TestPulseHistory:
    def test_take_snapshot_creates_history(self, _use_tmp: Path) -> None:
        from armillary.cache import Cache
        from armillary.models import Project, ProjectType
        from armillary.pulse_service import load_history, take_snapshot

        with Cache(db_path=_use_tmp) as cache:
            cache.upsert(
                [
                    Project(
                        path=Path("/tmp/test-proj"),
                        name="test-proj",
                        type=ProjectType.GIT,
                        umbrella=Path("/tmp"),
                        last_modified=datetime.now(),
                        metadata=ProjectMetadata(
                            status=Status.ACTIVE,
                            work_hours=50.0,
                            commit_count=100,
                            last_commit_ts=datetime.now(),
                        ),
                    )
                ]
            )

        snap = take_snapshot(db_path=_use_tmp)
        assert snap.active >= 1
        assert snap.total_hours >= 50.0

        history = load_history(db_path=_use_tmp)
        assert len(history) == 1
        assert history[0]["active"] >= 1

    def test_snapshot_dedupes_by_week(self, _use_tmp: Path) -> None:
        from armillary.cache import Cache
        from armillary.models import Project, ProjectType
        from armillary.pulse_service import load_history, take_snapshot

        with Cache(db_path=_use_tmp) as cache:
            cache.upsert(
                [
                    Project(
                        path=Path("/tmp/p"),
                        name="p",
                        type=ProjectType.GIT,
                        umbrella=Path("/tmp"),
                        last_modified=datetime.now(),
                        metadata=ProjectMetadata(status=Status.ACTIVE),
                    )
                ]
            )

        take_snapshot(db_path=_use_tmp)
        take_snapshot(db_path=_use_tmp)
        history = load_history(db_path=_use_tmp)
        assert len(history) == 1  # same week = dedupe

    def test_empty_history(self, _use_tmp: Path) -> None:
        from armillary.pulse_service import load_history

        assert load_history(db_path=_use_tmp) == []


# --- shareable card ---------------------------------------------------------


class TestShareableCard:
    def test_export_html_contains_stats(self) -> None:
        from datetime import date

        from armillary.heatmap_service import export_heatmap_html, heatmap_summary

        activity = {
            date(2026, 4, 1): 10,
            date(2026, 4, 2): 5,
            date(2026, 4, 3): 20,
        }
        summary = heatmap_summary(activity)
        html = export_heatmap_html(activity, summary)

        assert "armillary" in html
        assert "35" in html  # total commits
        assert "3" in html  # active days
        assert "<!DOCTYPE html>" in html

    def test_export_empty_activity(self) -> None:
        from armillary.heatmap_service import export_heatmap_html, heatmap_summary

        summary = heatmap_summary({})
        html = export_heatmap_html({}, summary)
        assert "armillary" in html
        assert "0" in html


# --- zombie alert -----------------------------------------------------------


class TestZombieAlert:
    def test_finds_stale_active_projects(self, _use_tmp: Path) -> None:
        """ACTIVE project with no commit in 14+ days = zombie."""
        from armillary.cache import Cache
        from armillary.exclude_service import filter_excluded
        from armillary.models import Project, ProjectType
        from armillary.status_override import filter_archived

        now = datetime.now()
        with Cache(db_path=_use_tmp) as cache:
            cache.upsert(
                [
                    Project(
                        path=Path("/tmp/zombie"),
                        name="zombie",
                        type=ProjectType.GIT,
                        umbrella=Path("/tmp"),
                        last_modified=now,
                        metadata=ProjectMetadata(
                            status=Status.ACTIVE,
                            work_hours=50.0,
                            last_commit_ts=now - timedelta(days=20),
                        ),
                    ),
                    Project(
                        path=Path("/tmp/alive"),
                        name="alive",
                        type=ProjectType.GIT,
                        umbrella=Path("/tmp"),
                        last_modified=now,
                        metadata=ProjectMetadata(
                            status=Status.ACTIVE,
                            work_hours=50.0,
                            last_commit_ts=now - timedelta(days=2),
                        ),
                    ),
                ]
            )

        cutoff = now - timedelta(days=14)
        with Cache(db_path=_use_tmp) as cache:
            projects = cache.list_projects()
        projects = filter_excluded(projects)
        projects = filter_archived(projects)
        zombies = [
            p
            for p in projects
            if p.metadata
            and p.metadata.status
            and p.metadata.status.value == "ACTIVE"
            and p.metadata.last_commit_ts
            and p.metadata.last_commit_ts < cutoff
            and (p.metadata.work_hours or 0) > 10
        ]
        assert len(zombies) == 1
        assert zombies[0].name == "zombie"


# --- bulk archive (logic test, not UI) --------------------------------------


class TestBulkArchive:
    def test_set_override_for_multiple_projects(self, _use_tmp: Path) -> None:
        from armillary.status_override import get_override, set_override

        paths = ["/tmp/a", "/tmp/b", "/tmp/c"]
        for p in paths:
            set_override(p, Status.ARCHIVED)

        for p in paths:
            assert get_override(p) == Status.ARCHIVED

    def test_filter_archived_removes_bulk(self, _use_tmp: Path) -> None:
        from armillary.models import Project, ProjectType
        from armillary.status_override import (
            filter_archived,
            set_override,
        )

        projects = [
            Project(
                path=Path(f"/tmp/{name}"),
                name=name,
                type=ProjectType.GIT,
                umbrella=Path("/tmp"),
                last_modified=datetime.now(),
                metadata=ProjectMetadata(status=Status.ACTIVE),
            )
            for name in ["a", "b", "c", "d"]
        ]
        # Archive 2 of 4
        set_override("/tmp/a", Status.ARCHIVED)
        set_override("/tmp/c", Status.ARCHIVED)

        filtered = filter_archived(projects)
        names = [p.name for p in filtered]
        assert "a" not in names
        assert "c" not in names
        assert "b" in names
        assert "d" in names

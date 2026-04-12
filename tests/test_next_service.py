"""Tests for `armillary.next_service` — recommendation engine."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from armillary.cache import Cache
from armillary.models import Project, ProjectMetadata, ProjectType, Status
from armillary.next_service import get_suggestions, skip_project

_NOW = datetime(2026, 4, 13, 10, 0, 0)


def _project(
    name: str,
    *,
    status: Status = Status.ACTIVE,
    work_hours: float = 100,
    last_commit_ts: datetime | None = None,
    dirty_count: int = 0,
    path: Path | None = None,
) -> Project:
    base = path or Path(f"/tmp/{name}")
    return Project(
        path=base,
        name=name,
        type=ProjectType.GIT,
        umbrella=base.parent,
        last_modified=_NOW,
        metadata=ProjectMetadata(
            status=status,
            work_hours=work_hours,
            last_commit_ts=last_commit_ts,
            dirty_count=dirty_count,
            commit_count=50,
        ),
    )


@pytest.fixture()
def _use_tmp_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "cache.db"
    monkeypatch.setenv("ARMILLARY_CACHE_DB", str(db_path))
    return db_path


def test_momentum_suggestion(_use_tmp_cache: Path) -> None:
    db_path = _use_tmp_cache
    with Cache(db_path=db_path) as cache:
        cache.upsert(
            [
                _project(
                    "active-proj",
                    status=Status.ACTIVE,
                    work_hours=200,
                    last_commit_ts=_NOW - timedelta(hours=2),
                    dirty_count=3,
                ),
            ]
        )

    results = get_suggestions(db_path=db_path, now=_NOW)
    assert len(results) == 1
    assert results[0].category == "momentum"
    assert "momentum" in results[0].reason.lower() or "dirty" in results[0].reason


def test_zombie_suggestion(_use_tmp_cache: Path) -> None:
    db_path = _use_tmp_cache
    with Cache(db_path=db_path) as cache:
        cache.upsert(
            [
                _project(
                    "zombie-proj",
                    status=Status.ACTIVE,
                    work_hours=80,
                    last_commit_ts=_NOW - timedelta(days=14),
                ),
            ]
        )

    results = get_suggestions(db_path=db_path, now=_NOW)
    assert len(results) == 1
    assert results[0].category == "zombie"
    assert "kill or ship" in results[0].reason.lower()


def test_forgotten_gold_suggestion(_use_tmp_cache: Path) -> None:
    db_path = _use_tmp_cache
    with Cache(db_path=db_path) as cache:
        cache.upsert(
            [
                _project(
                    "old-gem",
                    status=Status.DORMANT,
                    work_hours=163,
                    last_commit_ts=_NOW - timedelta(days=90),
                ),
            ]
        )

    results = get_suggestions(db_path=db_path, now=_NOW)
    assert len(results) == 1
    assert results[0].category == "forgotten_gold"
    assert "archive" in results[0].reason.lower()


def test_max_3_suggestions(_use_tmp_cache: Path) -> None:
    db_path = _use_tmp_cache
    with Cache(db_path=db_path) as cache:
        cache.upsert(
            [
                _project(
                    f"active-{i}",
                    status=Status.ACTIVE,
                    work_hours=100 + i * 10,
                    last_commit_ts=_NOW - timedelta(hours=i),
                )
                for i in range(10)
            ]
        )

    results = get_suggestions(db_path=db_path, now=_NOW)
    assert len(results) <= 3


def test_skip_excludes_project(
    _use_tmp_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _use_tmp_cache
    with Cache(db_path=db_path) as cache:
        cache.upsert(
            [
                _project(
                    "skip-me",
                    status=Status.DORMANT,
                    work_hours=200,
                    last_commit_ts=_NOW - timedelta(days=60),
                ),
            ]
        )

    # Before skip
    results = get_suggestions(db_path=db_path, now=_NOW)
    assert any(s.project.name == "skip-me" for s in results)

    # Skip it (use same _NOW for deterministic test)
    skip_project("/tmp/skip-me", now=_NOW, db_path=db_path)

    # After skip
    results = get_suggestions(db_path=db_path, now=_NOW)
    assert not any(s.project.name == "skip-me" for s in results)


def test_empty_cache_returns_empty(_use_tmp_cache: Path) -> None:
    db_path = _use_tmp_cache
    with Cache(db_path=db_path):
        pass

    results = get_suggestions(db_path=db_path, now=_NOW)
    assert results == []


def test_low_hours_dormant_not_suggested(_use_tmp_cache: Path) -> None:
    db_path = _use_tmp_cache
    with Cache(db_path=db_path) as cache:
        cache.upsert(
            [
                _project(
                    "tiny",
                    status=Status.DORMANT,
                    work_hours=5,
                    last_commit_ts=_NOW - timedelta(days=90),
                ),
            ]
        )

    results = get_suggestions(db_path=db_path, now=_NOW)
    assert results == []


def test_one_per_category_then_fill(_use_tmp_cache: Path) -> None:
    db_path = _use_tmp_cache
    with Cache(db_path=db_path) as cache:
        cache.upsert(
            [
                _project(
                    "momentum1",
                    status=Status.ACTIVE,
                    work_hours=300,
                    last_commit_ts=_NOW - timedelta(hours=1),
                ),
                _project(
                    "momentum2",
                    status=Status.ACTIVE,
                    work_hours=200,
                    last_commit_ts=_NOW - timedelta(hours=2),
                ),
                _project(
                    "zombie1",
                    status=Status.ACTIVE,
                    work_hours=100,
                    last_commit_ts=_NOW - timedelta(days=10),
                ),
                _project(
                    "gold1",
                    status=Status.DORMANT,
                    work_hours=150,
                    last_commit_ts=_NOW - timedelta(days=60),
                ),
            ]
        )

    results = get_suggestions(db_path=db_path, now=_NOW)
    categories = [s.category for s in results]
    # Should have one of each category first
    assert "momentum" in categories
    assert "zombie" in categories
    assert "forgotten_gold" in categories

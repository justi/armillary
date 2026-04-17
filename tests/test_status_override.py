"""Tests for status override persistence and filtering."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from armillary.models import Project, ProjectMetadata, ProjectType, Status
from armillary.status_override import (
    clear_override,
    filter_archived,
    get_override,
    load_overrides,
    set_override,
)


@pytest.fixture()
def _use_tmp_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "cache.db"
    monkeypatch.setenv("ARMILLARY_CACHE_DB", str(db_path))
    return db_path


def _project(
    name: str,
    *,
    status: Status = Status.ACTIVE,
    path: Path | None = None,
) -> Project:
    base = path or Path(f"/tmp/{name}")
    return Project(
        path=base,
        name=name,
        type=ProjectType.GIT,
        umbrella=base.parent,
        last_modified=datetime.now(),
        metadata=ProjectMetadata(status=status),
    )


def test_set_and_get_override(_use_tmp_overrides: Path) -> None:
    set_override("/tmp/proj", Status.ARCHIVED)
    assert get_override("/tmp/proj") == Status.ARCHIVED


def test_get_override_returns_none_when_not_set(_use_tmp_overrides: Path) -> None:
    assert get_override("/tmp/nonexistent") is None


def test_clear_override(_use_tmp_overrides: Path) -> None:
    set_override("/tmp/proj", Status.ARCHIVED)
    clear_override("/tmp/proj")
    assert get_override("/tmp/proj") is None


def test_clear_nonexistent_is_noop(_use_tmp_overrides: Path) -> None:
    clear_override("/tmp/nonexistent")  # should not raise


def test_load_overrides_empty_when_no_file(_use_tmp_overrides: Path) -> None:
    assert load_overrides() == {}


def test_roundtrip_multiple_overrides(_use_tmp_overrides: Path) -> None:
    set_override("/tmp/a", Status.ARCHIVED)
    set_override("/tmp/b", Status.ARCHIVED)
    overrides = load_overrides()
    assert len(overrides) == 2
    assert overrides["/tmp/a"] == "ARCHIVED"


def test_filter_archived_removes_archived_projects() -> None:
    projects = [
        _project("active", status=Status.ACTIVE),
        _project("archived", status=Status.ARCHIVED),
        _project("dormant", status=Status.DORMANT),
    ]
    filtered = filter_archived(projects)
    names = [p.name for p in filtered]
    assert "active" in names
    assert "dormant" in names
    assert "archived" not in names


def test_filter_archived_uses_override_not_just_cache(
    _use_tmp_overrides: Path,
) -> None:
    """Critical regression test: cache says ACTIVE, override says ARCHIVED.

    filter_archived must check the override file, not just metadata.status.
    This is the real failure mode — archiving without rescan.
    """
    proj = _project("sneaky", status=Status.ACTIVE, path=Path("/tmp/sneaky"))
    set_override("/tmp/sneaky", Status.ARCHIVED)

    filtered = filter_archived([proj])
    assert len(filtered) == 0, (
        "ACTIVE project with ARCHIVED override should be filtered"
    )


def test_filter_archived_keeps_all_when_none_archived() -> None:
    projects = [
        _project("a", status=Status.ACTIVE),
        _project("b", status=Status.PAUSED),
    ]
    assert len(filter_archived(projects)) == 2

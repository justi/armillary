"""Tests for share_service — tweet + HN post generation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from armillary.cache import Cache
from armillary.models import Project, ProjectMetadata, ProjectType, Status


def _project(
    name: str, *, status: Status = Status.ACTIVE, hours: float = 50
) -> Project:
    return Project(
        path=Path(f"/tmp/{name}"),
        name=name,
        type=ProjectType.GIT,
        umbrella=Path("/tmp"),
        last_modified=datetime.now(),
        metadata=ProjectMetadata(
            status=status,
            work_hours=hours,
            first_commit_ts=datetime(2020, 1, 1),
        ),
    )


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "cache.db"
    monkeypatch.setenv("ARMILLARY_CACHE_DB", str(db_path))
    return db_path


def test_generate_tweet_requires_at_least_five(db: Path) -> None:
    from armillary.share_service import generate_tweet

    with Cache(db_path=db) as c:
        c.upsert([_project(f"p{i}") for i in range(3)])
    assert "Scan more" in generate_tweet(db_path=db)


def test_generate_tweet_no_project_names(db: Path) -> None:
    from armillary.share_service import generate_tweet

    names = [f"secretproject{i}" for i in range(6)]
    with Cache(db_path=db) as c:
        c.upsert([_project(n) for n in names])
    tweet = generate_tweet(db_path=db)
    for n in names:
        assert n not in tweet


def test_generate_hn_requires_at_least_five(db: Path) -> None:
    from armillary.share_service import generate_hn_post

    with Cache(db_path=db) as c:
        c.upsert([_project(f"p{i}") for i in range(2)])
    assert "Scan more" in generate_hn_post(db_path=db)


def test_generate_hn_no_project_names(db: Path) -> None:
    from armillary.share_service import generate_hn_post

    names = [f"myrepo{i}" for i in range(6)]
    with Cache(db_path=db) as c:
        c.upsert([_project(n) for n in names])
    hn = generate_hn_post(db_path=db)
    for n in names:
        assert n not in hn

"""Tests for transition_service — detection + journal."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from armillary.cache import Cache
from armillary.models import Project, ProjectMetadata, ProjectType, Status


def _project(name: str, *, status: Status = Status.ACTIVE) -> Project:
    return Project(
        path=Path(f"/tmp/{name}"),
        name=name,
        type=ProjectType.GIT,
        umbrella=Path("/tmp"),
        last_modified=datetime.now(),
        metadata=ProjectMetadata(status=status, last_commit_ts=datetime.now()),
    )


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "cache.db"
    monkeypatch.setenv("ARMILLARY_CACHE_DB", str(db_path))
    return db_path


def test_first_run_baselines_without_transitions(db: Path) -> None:
    from armillary.transition_service import detect_and_store_transitions

    with Cache(db_path=db) as c:
        c.upsert([_project("a"), _project("b")])
    transitions = detect_and_store_transitions(db_path=db)
    assert transitions == []


def test_detects_status_change_and_records_journal(db: Path) -> None:
    from armillary.transition_service import detect_and_store_transitions, load_journal

    with Cache(db_path=db) as c:
        c.upsert([_project("a", status=Status.ACTIVE)])
    detect_and_store_transitions(db_path=db)  # baseline

    with Cache(db_path=db) as c:
        c.upsert([_project("a", status=Status.DORMANT)])
    transitions = detect_and_store_transitions(db_path=db)
    assert len(transitions) == 1
    assert transitions[0]["from_status"] == "ACTIVE"
    assert transitions[0]["to_status"] == "DORMANT"

    journal = load_journal("/tmp/a", db_path=db)
    assert len(journal) == 1
    assert journal[0]["from"] == "ACTIVE"
    assert journal[0]["to"] == "DORMANT"


def test_idempotent_for_unchanged_cache(db: Path) -> None:
    from armillary.transition_service import detect_and_store_transitions

    with Cache(db_path=db) as c:
        c.upsert([_project("a")])
    detect_and_store_transitions(db_path=db)  # baseline
    # No change — should return empty
    transitions = detect_and_store_transitions(db_path=db)
    assert transitions == []


def test_record_journal_entry_appends(db: Path) -> None:
    from armillary.transition_service import load_journal, record_journal_entry

    record_journal_entry("/tmp/x", "ACTIVE", "DORMANT", db_path=db)
    record_journal_entry(
        "/tmp/x", "DORMANT", "ACTIVE", reason="back to work", db_path=db
    )
    journal = load_journal("/tmp/x", db_path=db)
    assert len(journal) == 2
    assert journal[1]["reason"] == "back to work"


def test_load_journal_empty_for_missing(db: Path) -> None:
    from armillary.transition_service import load_journal

    assert load_journal("/tmp/nonexistent", db_path=db) == []

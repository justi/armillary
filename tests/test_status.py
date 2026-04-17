"""Status heuristics tests.

`compute_status` is pure on its inputs except for one filesystem read
of `TODO.md` for IDEA folders, so all tests inject a fixed `now` and
build `Project` / `ProjectMetadata` directly without touching git.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from armillary.models import Project, ProjectMetadata, ProjectType, Status
from armillary.status import compute_status

NOW = datetime(2026, 4, 11, 12, 0, 0)


def _git_project(
    *,
    path: Path = Path("/tmp/git-thing"),
    last_modified: datetime | None = None,
    metadata: ProjectMetadata | None = None,
) -> Project:
    return Project(
        path=path,
        name=path.name,
        type=ProjectType.GIT,
        umbrella=path.parent,
        last_modified=last_modified or NOW,
        metadata=metadata,
    )


def _idea_project(*, path: Path) -> Project:
    return Project(
        path=path,
        name=path.name,
        type=ProjectType.IDEA,
        umbrella=path.parent,
        last_modified=NOW,
    )


# --- ACTIVE ----------------------------------------------------------------


def test_active_when_recent_commit() -> None:
    md = ProjectMetadata(last_commit_ts=NOW - timedelta(days=2))
    assert compute_status(_git_project(metadata=md), now=NOW) is Status.ACTIVE


def test_active_when_recent_filesystem_edit_without_metadata() -> None:
    """No git metadata → fall back to project.last_modified."""
    p = _git_project(last_modified=NOW - timedelta(days=1))
    assert compute_status(p, now=NOW) is Status.ACTIVE


def test_dormant_when_clean_tree_in_paused_window() -> None:
    """Between active and paused cutoffs with a clean tree → DORMANT.

    PAUSED is reserved for repos with dirty files (PLAN.md §5: "PAUSED
    — dirty files + last commit 7-30 days ago"). A clean repo nobody
    has touched is just DORMANT regardless of whether it crossed the
    30-day mark yet.
    """
    md = ProjectMetadata(
        last_commit_ts=NOW - timedelta(days=14),
        dirty_count=0,
    )
    assert compute_status(_git_project(metadata=md), now=NOW) is Status.DORMANT


# --- PAUSED ---------------------------------------------------------------


def test_paused_when_dirty_in_window() -> None:
    md = ProjectMetadata(
        last_commit_ts=NOW - timedelta(days=14),
        dirty_count=3,
    )
    assert compute_status(_git_project(metadata=md), now=NOW) is Status.PAUSED


def test_paused_only_when_within_paused_cutoff() -> None:
    """Dirty + 31 days → DORMANT, not PAUSED."""
    md = ProjectMetadata(
        last_commit_ts=NOW - timedelta(days=31),
        dirty_count=5,
    )
    assert compute_status(_git_project(metadata=md), now=NOW) is Status.DORMANT


# --- DORMANT --------------------------------------------------------------


def test_dormant_when_old_clean_repo() -> None:
    md = ProjectMetadata(
        last_commit_ts=NOW - timedelta(days=120),
        dirty_count=0,
    )
    assert compute_status(_git_project(metadata=md), now=NOW) is Status.DORMANT


def test_dormant_when_metadata_missing_and_old_filesystem() -> None:
    p = _git_project(last_modified=NOW - timedelta(days=200))
    assert compute_status(p, now=NOW) is Status.DORMANT


# --- IDEA / IN_PROGRESS ---------------------------------------------------


def test_idea_for_loose_folder_without_todo(tmp_path: Path) -> None:
    folder = tmp_path / "thoughts"
    folder.mkdir()
    (folder / "notes.md").write_text("# notes")

    p = _idea_project(path=folder)
    assert compute_status(p, now=NOW) is Status.IDEA


def test_in_progress_when_todo_has_open_checkbox(tmp_path: Path) -> None:
    folder = tmp_path / "wip"
    folder.mkdir()
    (folder / "TODO.md").write_text("- [x] done\n- [ ] pending\n")

    p = _idea_project(path=folder)
    assert compute_status(p, now=NOW) is Status.IN_PROGRESS


def test_idea_when_todo_has_only_completed_checkboxes(tmp_path: Path) -> None:
    folder = tmp_path / "done"
    folder.mkdir()
    (folder / "TODO.md").write_text("- [x] done\n- [x] also done\n")

    p = _idea_project(path=folder)
    assert compute_status(p, now=NOW) is Status.IDEA


def test_idea_when_todo_missing(tmp_path: Path) -> None:
    folder = tmp_path / "no-todo"
    folder.mkdir()

    p = _idea_project(path=folder)
    assert compute_status(p, now=NOW) is Status.IDEA


# --- custom thresholds ----------------------------------------------------


def test_custom_active_days_overrides_default() -> None:
    md = ProjectMetadata(last_commit_ts=NOW - timedelta(days=10))
    p = _git_project(metadata=md)

    # default cutoff (7d) → not active
    assert compute_status(p, now=NOW) is not Status.ACTIVE
    # extended cutoff → active
    assert compute_status(p, now=NOW, active_days=14) is Status.ACTIVE


def test_custom_paused_days_overrides_default() -> None:
    md = ProjectMetadata(
        last_commit_ts=NOW - timedelta(days=45),
        dirty_count=2,
    )
    p = _git_project(metadata=md)

    # default cutoff (30d) → DORMANT
    assert compute_status(p, now=NOW) is Status.DORMANT
    # extended cutoff → PAUSED
    assert compute_status(p, now=NOW, paused_days=60) is Status.PAUSED


def test_manual_override_takes_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ARCHIVED override must survive regardless of git activity."""
    db_path = tmp_path / "cache.db"
    monkeypatch.setenv("ARMILLARY_CACHE_DB", str(db_path))

    from armillary.status_override import clear_override, set_override

    # Active project by heuristic (commit 1 hour ago)
    md = ProjectMetadata(last_commit_ts=NOW - timedelta(hours=1))
    p = _git_project(path=tmp_path / "proj", metadata=md)

    assert compute_status(p, now=NOW) is Status.ACTIVE

    # Set ARCHIVED override
    set_override(str(p.path), Status.ARCHIVED)
    assert compute_status(p, now=NOW) is Status.ARCHIVED

    # Clear override — returns to auto
    clear_override(str(p.path))
    assert compute_status(p, now=NOW) is Status.ACTIVE

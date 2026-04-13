"""Tests for `armillary.context_service` — project re-entry context."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from armillary.cache import Cache
from armillary.context_service import BranchInfo, CommitInfo, get_context
from armillary.models import Project, ProjectMetadata, ProjectType, Status

_NOW = datetime(2026, 4, 13, 10, 0, 0)


# --- helpers ----------------------------------------------------------------


def _project(
    name: str,
    *,
    status: Status = Status.ACTIVE,
    work_hours: float = 42.0,
    project_type: ProjectType = ProjectType.GIT,
    path: Path | None = None,
) -> Project:
    base = path or Path(f"/tmp/{name}")
    return Project(
        path=base,
        name=name,
        type=project_type,
        umbrella=base.parent,
        last_modified=_NOW,
        metadata=ProjectMetadata(
            status=status,
            work_hours=work_hours,
        ),
    )


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated cache DB for every test."""
    path = tmp_path / "cache.db"
    monkeypatch.setenv("ARMILLARY_CACHE_DB", str(path))
    return path


def _seed(db_path: Path, projects: list[Project]) -> None:
    """Insert projects into the cache."""
    with Cache(db_path=db_path) as cache:
        cache.upsert(projects)


def _mock_subprocess(
    monkeypatch: pytest.MonkeyPatch, responses: dict[tuple[str, ...], str]
) -> None:
    """Replace subprocess.run with a mock that returns canned responses.

    `responses` maps a tuple of git args (everything after "git") to
    the stdout string. Unmatched commands return empty stdout with
    returncode 1.
    """

    def fake_run(cmd, *, cwd=None, capture_output=False, text=False, timeout=None):
        git_args = tuple(cmd[1:])  # strip "git" prefix
        result = MagicMock()
        if git_args in responses:
            result.stdout = responses[git_args]
            result.returncode = 0
        else:
            result.stdout = ""
            result.returncode = 1
        return result

    monkeypatch.setattr("armillary.context_service.subprocess.run", fake_run)


# --- test: non-existent project returns None --------------------------------


def test_returns_none_for_nonexistent_project(db_path: Path) -> None:
    _seed(db_path, [_project("alpha")])
    result = get_context("zzz-no-such-project", db_path=db_path)
    assert result is None


# --- test: existing git project with mocked git commands --------------------


def test_returns_context_for_existing_git_project(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir = tmp_path / "myapp"
    project_dir.mkdir()
    (project_dir / ".git").mkdir()

    _seed(db_path, [_project("myapp", path=project_dir, work_hours=100.0)])

    _mock_subprocess(
        monkeypatch,
        {
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain", "--no-renames"): "M  README.md\n?? tmp.txt",
            (
                "log",
                "-5",
                "--format=%h\t%ar\t%s",
            ): "abc1234\t2 hours ago\tfix tests\ndef5678\t3 days ago\tinitial commit",
            (
                "branch",
                "--sort=-committerdate",
                "--format=%(refname:short)\t%(committerdate:relative)",
            ): "main\t2 hours ago\ndev\t5 days ago\nfeature-x\t1 week ago",
        },
    )

    ctx = get_context("myapp", db_path=db_path)

    assert ctx is not None
    assert ctx.name == "myapp"
    assert ctx.path == project_dir
    assert ctx.status == "ACTIVE"
    assert ctx.work_hours == 100.0
    assert ctx.branch == "main"
    assert ctx.is_git is True
    assert ctx.dirty_count == 2
    assert len(ctx.dirty_files) == 2
    assert "M  README.md" in ctx.dirty_files
    assert len(ctx.recent_commits) == 2
    assert ctx.recent_commits[0] == CommitInfo(
        short_hash="abc1234", relative_time="2 hours ago", subject="fix tests"
    )
    # "main" is current branch so it is excluded; "dev" and "feature-x" remain
    assert len(ctx.recent_branches) == 2
    assert ctx.recent_branches[0] == BranchInfo(name="dev", relative_time="5 days ago")


# --- test: IDEA project returns is_git=False --------------------------------


def test_idea_project_returns_is_git_false(db_path: Path, tmp_path: Path) -> None:
    idea_dir = tmp_path / "sketch"
    idea_dir.mkdir()
    # No .git directory

    _seed(
        db_path,
        [
            _project(
                "sketch",
                path=idea_dir,
                project_type=ProjectType.IDEA,
                status=Status.IDEA,
            )
        ],
    )

    ctx = get_context("sketch", db_path=db_path)

    assert ctx is not None
    assert ctx.is_git is False
    assert ctx.branch is None
    assert ctx.dirty_files == []
    assert ctx.dirty_count == 0
    assert ctx.recent_commits == []
    assert ctx.recent_branches == []


# --- test: ambiguous name raises ValueError ---------------------------------


def test_raises_valueerror_on_ambiguous_name(db_path: Path) -> None:
    _seed(
        db_path,
        [
            _project("app-frontend"),
            _project("app-backend"),
            _project("app-worker"),
        ],
    )

    with pytest.raises(ValueError, match="Ambiguous"):
        get_context("app", db_path=db_path)


# --- test: exact match wins over substring ----------------------------------


def test_exact_match_wins_over_substring(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exact_dir = tmp_path / "blog"
    exact_dir.mkdir()
    (exact_dir / ".git").mkdir()

    _seed(
        db_path,
        [
            _project("blog", path=exact_dir),
            _project("blog-v2"),
            _project("blog-archive"),
        ],
    )

    _mock_subprocess(
        monkeypatch,
        {
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain", "--no-renames"): "",
            ("log", "-5", "--format=%h\t%ar\t%s"): "",
            (
                "branch",
                "--sort=-committerdate",
                "--format=%(refname:short)\t%(committerdate:relative)",
            ): "",
        },
    )

    ctx = get_context("blog", db_path=db_path)

    assert ctx is not None
    assert ctx.name == "blog"


# --- test: project with 0 commits ------------------------------------------


def test_handles_zero_commits_gracefully(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj_dir = tmp_path / "empty-repo"
    proj_dir.mkdir()
    (proj_dir / ".git").mkdir()

    _seed(db_path, [_project("empty-repo", path=proj_dir)])

    _mock_subprocess(
        monkeypatch,
        {
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain", "--no-renames"): "",
            ("log", "-5", "--format=%h\t%ar\t%s"): "",
            (
                "branch",
                "--sort=-committerdate",
                "--format=%(refname:short)\t%(committerdate:relative)",
            ): "",
        },
    )

    ctx = get_context("empty-repo", db_path=db_path)

    assert ctx is not None
    assert ctx.recent_commits == []
    assert ctx.recent_branches == []
    assert ctx.dirty_files == []
    assert ctx.dirty_count == 0
    assert ctx.branch == "main"


# --- test: dirty_files capped at 5 items -----------------------------------


def test_dirty_files_capped_at_five(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj_dir = tmp_path / "big-wip"
    proj_dir.mkdir()
    (proj_dir / ".git").mkdir()

    _seed(db_path, [_project("big-wip", path=proj_dir)])

    dirty_lines = "\n".join(f"M  file{i}.rb" for i in range(10))
    _mock_subprocess(
        monkeypatch,
        {
            ("rev-parse", "--abbrev-ref", "HEAD"): "feature",
            ("status", "--porcelain", "--no-renames"): dirty_lines,
            ("log", "-5", "--format=%h\t%ar\t%s"): "aaa\t1 hour ago\twip",
            (
                "branch",
                "--sort=-committerdate",
                "--format=%(refname:short)\t%(committerdate:relative)",
            ): "feature\t1 hour ago",
        },
    )

    ctx = get_context("big-wip", db_path=db_path)

    assert ctx is not None
    assert len(ctx.dirty_files) == 5
    # dirty_count reflects the real total, not the capped list
    assert ctx.dirty_count == 10


# --- test: case-insensitive substring match ---------------------------------


def test_case_insensitive_match(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj_dir = tmp_path / "MyProject"
    proj_dir.mkdir()
    (proj_dir / ".git").mkdir()

    _seed(db_path, [_project("MyProject", path=proj_dir)])

    _mock_subprocess(
        monkeypatch,
        {
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain", "--no-renames"): "",
            ("log", "-5", "--format=%h\t%ar\t%s"): "",
            (
                "branch",
                "--sort=-committerdate",
                "--format=%(refname:short)\t%(committerdate:relative)",
            ): "",
        },
    )

    ctx = get_context("myproject", db_path=db_path)

    assert ctx is not None
    assert ctx.name == "MyProject"


# --- test: project with no metadata ----------------------------------------


def test_project_without_metadata(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj_dir = tmp_path / "bare"
    proj_dir.mkdir()
    (proj_dir / ".git").mkdir()

    bare = Project(
        path=proj_dir,
        name="bare",
        type=ProjectType.GIT,
        umbrella=proj_dir.parent,
        last_modified=_NOW,
        metadata=None,
    )
    _seed(db_path, [bare])

    _mock_subprocess(
        monkeypatch,
        {
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain", "--no-renames"): "",
            ("log", "-5", "--format=%h\t%ar\t%s"): "",
            (
                "branch",
                "--sort=-committerdate",
                "--format=%(refname:short)\t%(committerdate:relative)",
            ): "",
        },
    )

    ctx = get_context("bare", db_path=db_path)

    assert ctx is not None
    assert ctx.status is None
    assert ctx.work_hours is None

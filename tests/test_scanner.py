"""Scanner unit tests — fake folder trees via tmp_path."""

from __future__ import annotations

from pathlib import Path

import pytest

from armillary.models import ProjectType, UmbrellaFolder
from armillary.scanner import scan, scan_umbrella


# --- fixture builders -------------------------------------------------------


def _mkrepo(path: Path) -> Path:
    """Make `path` look like a git repo (no real git, just the marker)."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    (path / "README.md").write_text("# " + path.name)
    return path


def _mkidea(path: Path, *, file: str = "notes.md") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / file).write_text("loose idea")
    return path


def _umbrella(path: Path, **kw: object) -> UmbrellaFolder:
    return UmbrellaFolder(path=path, **kw)  # type: ignore[arg-type]


# --- tests ------------------------------------------------------------------


def test_empty_umbrella_returns_nothing(tmp_path: Path) -> None:
    assert scan_umbrella(_umbrella(tmp_path)) == []


def test_nonexistent_umbrella_returns_nothing(tmp_path: Path) -> None:
    assert scan_umbrella(_umbrella(tmp_path / "nope")) == []


def test_detects_flat_git_projects(tmp_path: Path) -> None:
    _mkrepo(tmp_path / "alpha")
    _mkrepo(tmp_path / "beta")

    projects = scan_umbrella(_umbrella(tmp_path))

    assert len(projects) == 2
    assert {p.name for p in projects} == {"alpha", "beta"}
    assert all(p.type is ProjectType.GIT for p in projects)
    assert all(p.umbrella == tmp_path.resolve() for p in projects)


def test_detects_idea_projects(tmp_path: Path) -> None:
    _mkidea(tmp_path / "thoughts")
    _mkidea(tmp_path / "notebook-area", file="explore.ipynb")

    projects = scan_umbrella(_umbrella(tmp_path))

    assert {p.name for p in projects} == {"thoughts", "notebook-area"}
    assert all(p.type is ProjectType.IDEA for p in projects)


def test_git_takes_precedence_over_idea(tmp_path: Path) -> None:
    """A folder with both .git and .md files is a git project."""
    p = _mkrepo(tmp_path / "mixed")
    (p / "notes.md").write_text("notes")

    projects = scan_umbrella(_umbrella(tmp_path))

    assert len(projects) == 1
    assert projects[0].type is ProjectType.GIT


def test_does_not_descend_into_git_repo(tmp_path: Path) -> None:
    """A sub-directory inside a git repo must not become its own project."""
    repo = _mkrepo(tmp_path / "outer")
    nested = repo / "subpkg"
    nested.mkdir()
    (nested / "README.md").write_text("inner")

    projects = scan_umbrella(_umbrella(tmp_path))

    assert len(projects) == 1
    assert projects[0].name == "outer"


def test_skips_default_ignores(tmp_path: Path) -> None:
    _mkrepo(tmp_path / "real")
    # These should never be walked into.
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "leftpad").mkdir()
    (tmp_path / "node_modules" / "leftpad" / ".git").mkdir()
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "pkg").mkdir()
    (tmp_path / ".venv" / "pkg" / "doc.md").write_text("x")

    projects = scan_umbrella(_umbrella(tmp_path))

    assert [p.name for p in projects] == ["real"]


def test_skips_hidden_dirs(tmp_path: Path) -> None:
    _mkidea(tmp_path / ".secret-cache")
    _mkidea(tmp_path / "visible")

    projects = scan_umbrella(_umbrella(tmp_path))

    assert [p.name for p in projects] == ["visible"]


def test_respects_max_depth(tmp_path: Path) -> None:
    # depth layout:
    #   tmp_path / lvl1 / lvl2 / repo
    deep = tmp_path / "lvl1" / "lvl2"
    deep.mkdir(parents=True)
    _mkrepo(deep / "repo")

    # max_depth=2 can only reach lvl1/lvl2, not lvl1/lvl2/repo
    projects = scan_umbrella(_umbrella(tmp_path, max_depth=2))
    assert projects == []

    # max_depth=3 can reach repo
    projects = scan_umbrella(_umbrella(tmp_path, max_depth=3))
    assert [p.name for p in projects] == ["repo"]


def test_recurses_through_intermediate_folders(tmp_path: Path) -> None:
    """An intermediate folder with no md/ipynb and no .git keeps descending."""
    mid = tmp_path / "work"
    mid.mkdir()
    _mkrepo(mid / "alpha")
    _mkrepo(mid / "beta")

    projects = scan_umbrella(_umbrella(tmp_path))

    assert {p.name for p in projects} == {"alpha", "beta"}


def test_scan_merges_multiple_umbrellas(tmp_path: Path) -> None:
    a = tmp_path / "A"
    b = tmp_path / "B"
    a.mkdir()
    b.mkdir()
    _mkrepo(a / "one")
    _mkrepo(b / "two")

    projects = scan([_umbrella(a), _umbrella(b)])

    assert {p.name for p in projects} == {"one", "two"}
    assert {p.umbrella for p in projects} == {a.resolve(), b.resolve()}


def test_umbrella_root_is_never_a_project(tmp_path: Path) -> None:
    """Even if the umbrella root itself has .md files, don't emit it."""
    (tmp_path / "README.md").write_text("root readme")

    projects = scan_umbrella(_umbrella(tmp_path))

    assert projects == []


@pytest.mark.parametrize("suffix", [".md", ".ipynb"])
def test_idea_detection_accepts_md_and_ipynb(tmp_path: Path, suffix: str) -> None:
    target = tmp_path / "thing"
    target.mkdir()
    (target / f"file{suffix}").write_text("x")

    projects = scan_umbrella(_umbrella(tmp_path))

    assert len(projects) == 1
    assert projects[0].type is ProjectType.IDEA

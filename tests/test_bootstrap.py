"""Tests for `armillary.bootstrap` (PLAN.md §5 Phase 1 — umbrella discovery).

Each test builds a fake `~/` under `tmp_path` and asserts that
`discover_umbrella_candidates(home=...)` returns the expected
candidates. We never touch the user's real home directory.
"""

from __future__ import annotations

from pathlib import Path

from armillary.bootstrap import (
    UmbrellaCandidate,
    _count_children,
    _should_inspect,
    discover_umbrella_candidates,
)


def _mkrepo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    (path / "README.md").write_text("# " + path.name)
    return path


def _mkidea(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "notes.md").write_text("notes")
    return path


# --- discover_umbrella_candidates -----------------------------------------


def test_empty_home_returns_no_candidates(tmp_path: Path) -> None:
    home = tmp_path / "empty"
    home.mkdir()
    assert discover_umbrella_candidates(home=home) == []


def test_nonexistent_home_returns_no_candidates(tmp_path: Path) -> None:
    assert discover_umbrella_candidates(home=tmp_path / "nope") == []


def test_finds_folder_with_two_git_repos(tmp_path: Path) -> None:
    home = tmp_path / "home"
    work = home / "random-folder-name"
    _mkrepo(work / "alpha")
    _mkrepo(work / "beta")

    candidates = discover_umbrella_candidates(home=home)

    assert len(candidates) == 1
    assert candidates[0].path.name == "random-folder-name"
    assert candidates[0].git_count == 2
    assert candidates[0].idea_count == 0


def test_skips_folder_with_only_one_git_repo_unless_named(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    work = home / "random-folder-name"
    _mkrepo(work / "lonely")

    # Below the threshold AND not a conventional name → skipped.
    assert discover_umbrella_candidates(home=home) == []


def test_finds_conventional_name_even_with_one_repo(tmp_path: Path) -> None:
    """A folder named 'Projects' (or 'projects', 'repos', ...) is always
    a candidate even if it has just one git repo, as long as it's not
    completely empty."""
    home = tmp_path / "home"
    projects = home / "Projects"
    _mkrepo(projects / "lonely")

    candidates = discover_umbrella_candidates(home=home)

    assert len(candidates) == 1
    assert candidates[0].path.name == "Projects"
    assert candidates[0].name_match is True


def test_finds_idea_only_conventional_folder(tmp_path: Path) -> None:
    home = tmp_path / "home"
    notes = home / "code"
    _mkidea(notes / "thoughts")

    candidates = discover_umbrella_candidates(home=home)

    assert len(candidates) == 1
    assert candidates[0].idea_count == 1
    assert candidates[0].git_count == 0


def test_skips_empty_conventional_folder(tmp_path: Path) -> None:
    """A folder named `Projects` but completely empty must NOT show up
    in the picker — it would just clutter the list."""
    home = tmp_path / "home"
    (home / "Projects").mkdir(parents=True)

    assert discover_umbrella_candidates(home=home) == []


def test_skips_system_folders(tmp_path: Path) -> None:
    home = tmp_path / "home"
    # System folders that we should never inspect even if they contain repos
    for forbidden in ("Library", "Applications", "Downloads", "Desktop"):
        d = home / forbidden / "fake-project"
        _mkrepo(d)

    assert discover_umbrella_candidates(home=home) == []


def test_skips_hidden_folders(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _mkrepo(home / ".secret-cache" / "thing-a")
    _mkrepo(home / ".secret-cache" / "thing-b")

    assert discover_umbrella_candidates(home=home) == []


def test_multiple_candidates_sorted_by_score(tmp_path: Path) -> None:
    """Folder with more git repos sorts before folder with fewer."""
    home = tmp_path / "home"

    big = home / "big-workspace"
    for i in range(5):
        _mkrepo(big / f"r{i}")

    small = home / "small-workspace"
    for i in range(2):
        _mkrepo(small / f"r{i}")

    candidates = discover_umbrella_candidates(home=home)

    assert len(candidates) == 2
    assert candidates[0].path.name == "big-workspace"
    assert candidates[1].path.name == "small-workspace"


def test_idea_inside_umbrella_counted(tmp_path: Path) -> None:
    home = tmp_path / "home"
    work = home / "Projects"
    _mkrepo(work / "real-repo")
    _mkidea(work / "idea-folder")
    _mkidea(work / "another-idea")

    [candidate] = discover_umbrella_candidates(home=home)
    assert candidate.git_count == 1
    assert candidate.idea_count == 2
    assert candidate.total_projects == 3


def test_does_not_recurse_past_immediate_children(tmp_path: Path) -> None:
    """`_count_children` only looks at direct children of the umbrella —
    deep-nested repos do not contribute to the score."""
    home = tmp_path / "home"
    work = home / "work"
    _mkrepo(work / "alpha")
    _mkrepo(work / "alpha" / "nested" / "deep")
    _mkrepo(work / "alpha" / "nested" / "deeper")

    [candidate] = discover_umbrella_candidates(home=home)
    assert candidate.git_count == 1  # only alpha, not the nested ones


# --- _should_inspect ------------------------------------------------------


def test_should_inspect_skips_files(tmp_path: Path) -> None:
    f = tmp_path / "not-a-dir.txt"
    f.write_text("x")
    assert _should_inspect(f) is False


def test_should_inspect_skips_hidden(tmp_path: Path) -> None:
    d = tmp_path / ".hidden"
    d.mkdir()
    assert _should_inspect(d) is False


def test_should_inspect_skips_known_system(tmp_path: Path) -> None:
    d = tmp_path / "Library"
    d.mkdir()
    assert _should_inspect(d) is False


def test_should_inspect_accepts_normal_dir(tmp_path: Path) -> None:
    d = tmp_path / "regular-folder"
    d.mkdir()
    assert _should_inspect(d) is True


# --- _count_children -----------------------------------------------------


def test_count_children_handles_unreadable_subfolder(tmp_path: Path) -> None:
    """A child whose contents we cannot list (no permission) just gets
    skipped — does not crash the count."""
    work = tmp_path / "work"
    _mkrepo(work / "real")
    weird = work / "weird"
    weird.mkdir()
    weird.chmod(0o000)
    try:
        git_count, _idea_count = _count_children(work)
    finally:
        weird.chmod(0o755)
    assert git_count == 1


def test_count_children_skips_noise_folders(tmp_path: Path) -> None:
    work = tmp_path / "work"
    _mkrepo(work / "real")
    # Pretend node_modules is a "git repo" — should not count
    (work / "node_modules" / ".git").mkdir(parents=True)

    git_count, _ = _count_children(work)
    assert git_count == 1


# --- UmbrellaCandidate ----------------------------------------------------


def test_score_orders_correctly(tmp_path: Path) -> None:
    from datetime import datetime as _dt

    a = UmbrellaCandidate(
        path=Path("/tmp/a"),
        git_count=5,
        idea_count=0,
        last_modified=_dt(2025, 1, 1),
        name_match=False,
    )
    b = UmbrellaCandidate(
        path=Path("/tmp/b"),
        git_count=3,
        idea_count=10,
        last_modified=_dt(2026, 1, 1),
        name_match=True,
    )
    # `a` has more git repos → wins on the first key
    assert a.score > b.score

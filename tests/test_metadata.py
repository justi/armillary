"""Metadata extraction tests.

Two layers:

- Real-git tests build actual `git init` repos in `tmp_path` and verify
  that GitPython sees the expected branch / commit / dirty count. These
  catch real-world wiring issues that mocks cannot.
- Mock-driven tests stub `git.Repo` to simulate broken repos and ensure
  the extractor swallows the exception cleanly.

Module-private helpers (`_first_paragraph_plain`, `_find_adr_files`,
`_extract_readme_excerpt`) get focused tests because they have many
edge cases (markdown headers, code fences, encoding).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from armillary import metadata
from armillary.metadata import (
    _extract_readme_excerpt,
    _find_adr_files,
    _first_paragraph_plain,
)
from armillary.models import Project, ProjectType

# --- helpers ----------------------------------------------------------------


def _mk_real_git_repo(
    path: Path,
    *,
    branch: str = "main",
    commit_msg: str = "initial",
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Initialise a real git repo with one commit at `path`.

    Uses `subprocess` directly so we don't take a hard dependency on the
    GitPython API in fixtures — keeping fixtures plain `git` lets us
    catch real GitPython contract drift.
    """
    path.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test Author",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=path, check=True, env=env)
    (path / "README.md").write_text(f"# {path.name}\n\nA test project.")
    for rel, content in (extra_files or {}).items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", commit_msg], cwd=path, check=True, env=env
    )
    return path


def _git_project(path: Path) -> Project:
    return Project(
        path=path.resolve(),
        name=path.name,
        type=ProjectType.GIT,
        umbrella=path.parent.resolve(),
        last_modified=__import__("datetime").datetime.now(),
    )


# --- real git: branch, commit, author, dirty count -------------------------


def test_extract_returns_branch_and_commit_for_real_repo(tmp_path: Path) -> None:
    repo = _mk_real_git_repo(tmp_path / "real", branch="trunk")

    md = metadata.extract(_git_project(repo))

    assert md.branch == "trunk"
    assert md.last_commit_sha is not None
    assert len(md.last_commit_sha) == 40
    assert md.last_commit_author == "Test Author"
    assert md.last_commit_ts is not None
    assert md.dirty_count == 0


def test_extract_counts_dirty_modified_and_untracked(tmp_path: Path) -> None:
    repo = _mk_real_git_repo(tmp_path / "dirty")
    # Modify a tracked file and add an untracked one.
    (repo / "README.md").write_text("# changed")
    (repo / "new-file.txt").write_text("untracked")

    md = metadata.extract(_git_project(repo))

    assert md.dirty_count == 2  # 1 modified + 1 untracked


def test_extract_handles_detached_head(tmp_path: Path) -> None:
    """Detached HEAD must not raise; branch falls back to None."""
    repo = _mk_real_git_repo(tmp_path / "detached")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "checkout", "-q", sha], cwd=repo, check=True)

    md = metadata.extract(_git_project(repo))

    assert md.branch is None
    assert md.last_commit_sha == sha


def test_extract_idea_project_skips_git_fields(tmp_path: Path) -> None:
    folder = tmp_path / "thoughts"
    folder.mkdir()
    (folder / "notes.md").write_text("# notes\n\nSome thoughts.")

    project = Project(
        path=folder.resolve(),
        name="thoughts",
        type=ProjectType.IDEA,
        umbrella=tmp_path.resolve(),
        last_modified=__import__("datetime").datetime.now(),
    )
    md = metadata.extract(project)

    assert md.branch is None
    assert md.last_commit_sha is None
    assert md.dirty_count is None
    # README excerpt is still extracted for idea projects.
    assert md.readme_excerpt is None  # this folder has no README.md
    # but it does have notes.md, which is not picked as README.


def test_extract_broken_repo_returns_empty_metadata(tmp_path: Path) -> None:
    """A folder with `.git` that is NOT a real repo must not crash."""
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / ".git").mkdir()  # not a real git repo
    (fake / "README.md").write_text("# fake\n\nA fake project for tests.")

    md = metadata.extract(_git_project(fake))

    # Git fields stay None — extraction caught the GitPython error.
    assert md.branch is None
    assert md.last_commit_sha is None
    # README is still picked up since that path is independent.
    assert md.readme_excerpt is not None
    assert "fake project" in md.readme_excerpt


# --- README extraction ----------------------------------------------------


def test_extract_readme_excerpt_picks_first_paragraph(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# Title\n\nFirst paragraph here.\n\nSecond paragraph that should be ignored."
    )
    excerpt = _extract_readme_excerpt(tmp_path)
    assert excerpt == "First paragraph here."


def test_extract_readme_excerpt_handles_no_readme(tmp_path: Path) -> None:
    assert _extract_readme_excerpt(tmp_path) is None


def test_first_paragraph_plain_strips_headers_and_inline_links() -> None:
    src = (
        "# Big title\n"
        "## Subtitle\n"
        "\n"
        "armillary is a [meta layer](https://example.com) over your "
        "`projects` folder. It does the boring stuff.\n"
    )
    assert _first_paragraph_plain(src) == (
        "armillary is a meta layer over your projects folder. It does the boring stuff."
    )


def test_first_paragraph_plain_skips_code_fences() -> None:
    src = "```bash\necho skip me\n```\n\nReal text that should appear.\n"
    assert _first_paragraph_plain(src) == "Real text that should appear."


def test_first_paragraph_plain_truncates_long_text() -> None:
    long_word = "lorem ipsum " * 100  # well over 280 chars
    out = _first_paragraph_plain(long_word)
    assert out is not None
    assert out.endswith("…")
    assert len(out) <= 281  # 280 + ellipsis


def test_first_paragraph_plain_returns_none_for_empty() -> None:
    assert _first_paragraph_plain("") is None
    assert _first_paragraph_plain("# only a header\n") is None


# --- ADR detection --------------------------------------------------------


def test_find_adr_files_picks_up_conventional_dirs(tmp_path: Path) -> None:
    (tmp_path / "adr").mkdir()
    (tmp_path / "adr" / "0001-use-sqlite.md").write_text("ADR 1")
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr" / "0002-pick-streamlit.md").write_text("ADR 2")
    (tmp_path / "decisions").mkdir()
    (tmp_path / "decisions" / "0003-no-cloud.md").write_text("ADR 3")

    found = _find_adr_files(tmp_path)
    names = {p.name for p in found}
    assert names == {
        "0001-use-sqlite.md",
        "0002-pick-streamlit.md",
        "0003-no-cloud.md",
    }


def test_find_adr_files_returns_empty_when_no_adr(tmp_path: Path) -> None:
    assert _find_adr_files(tmp_path) == []


def test_find_adr_files_only_matches_md(tmp_path: Path) -> None:
    (tmp_path / "adr").mkdir()
    (tmp_path / "adr" / "intro.txt").write_text("nope")
    (tmp_path / "adr" / "0001.md").write_text("yes")

    found = _find_adr_files(tmp_path)
    assert [p.name for p in found] == ["0001.md"]


# --- extract_all (parallel) ------------------------------------------------


def test_extract_all_attaches_metadata_to_each_project(tmp_path: Path) -> None:
    repos = [
        _mk_real_git_repo(tmp_path / "a", commit_msg="a-commit"),
        _mk_real_git_repo(tmp_path / "b", commit_msg="b-commit"),
        _mk_real_git_repo(tmp_path / "c", commit_msg="c-commit"),
    ]
    projects = [_git_project(p) for p in repos]
    for p in projects:
        assert p.metadata is None

    metadata.extract_all(projects, workers=2)

    for p in projects:
        assert p.metadata is not None
        assert p.metadata.last_commit_sha is not None
        assert p.metadata.last_commit_author == "Test Author"


def test_extract_all_handles_empty_list() -> None:
    metadata.extract_all([])  # must not crash


def test_extract_all_swallows_extraction_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `extract` raises for one project, the others still get metadata."""
    good = _mk_real_git_repo(tmp_path / "good")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / ".git").mkdir()  # broken repo

    projects = [_git_project(good), _git_project(bad)]
    metadata.extract_all(projects, workers=2)

    # Good project: real git data
    assert projects[0].metadata is not None
    assert projects[0].metadata.last_commit_sha is not None
    # Broken project: empty metadata, no exception
    assert projects[1].metadata is not None
    assert projects[1].metadata.last_commit_sha is None

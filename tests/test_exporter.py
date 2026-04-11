"""Tests for `armillary.exporter` (M7a repos-index generator).

Two layers:
- Pure render tests on `render_repos_index` with hand-built `Project`
  / `ProjectMetadata` instances and a frozen `generated_at` timestamp.
- Round-trip tests via `write_repos_index` on a real Cache file in
  tmp_path, exercising the cache → markdown path end-to-end.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from armillary.cache import Cache
from armillary.exporter import (
    _escape_for_markdown_table,
    render_repos_index,
    write_repos_index,
)
from armillary.models import Project, ProjectMetadata, ProjectType, Status

_NOW = datetime(2026, 4, 11, 12, 0, 0)


def _project(
    name: str,
    *,
    type: ProjectType = ProjectType.GIT,
    metadata: ProjectMetadata | None = None,
    last_modified: datetime | None = None,
    path: Path | None = None,
) -> Project:
    base = path or Path(f"/tmp/{name}")
    return Project(
        path=base,
        name=name,
        type=type,
        umbrella=base.parent,
        last_modified=last_modified or _NOW,
        metadata=metadata,
    )


# --- render_repos_index ---------------------------------------------------


def test_render_includes_heading_and_count() -> None:
    out = render_repos_index([], generated_at=_NOW)
    assert "# armillary — projects index" in out
    assert "**0** project(s)" in out
    assert "Cache is empty" in out


def test_render_handles_one_full_metadata_project() -> None:
    md = ProjectMetadata(
        branch="main",
        last_commit_sha="abcd1234",
        last_commit_ts=datetime(2026, 4, 1, 9, 0, 0),
        last_commit_author="Justyna",
        dirty_count=3,
        readme_excerpt="A meta layer over your projects.",
        status=Status.ACTIVE,
    )
    out = render_repos_index(
        [_project("alpha", metadata=md)],
        generated_at=_NOW,
    )
    assert "**1** project(s)" in out
    # Headers are present
    assert "| Name |" in out
    assert "| Status |" in out.replace(" Branch", "")
    # Body row contains the values
    assert "| alpha |" in out
    assert "| git |" in out
    assert "| ACTIVE |" in out
    assert "| main |" in out
    assert "| 3 |" in out
    assert "2026-04-01" in out  # last commit
    assert "A meta layer over your projects." in out


def test_render_uses_dash_for_missing_metadata() -> None:
    out = render_repos_index([_project("bare")], generated_at=_NOW)
    # Find the row with `bare` and verify several "—" entries
    bare_row = next(line for line in out.split("\n") if line.startswith("| bare "))
    assert "—" in bare_row
    # type is still git
    assert "| git |" in bare_row
    # status, branch, dirty, last_commit, readme are dashes
    assert bare_row.count("—") >= 5


def test_render_distinguishes_clean_from_unknown_dirty() -> None:
    """`dirty=0` (clean tree) is meaningful and must NOT collapse to '—'."""
    clean = ProjectMetadata(dirty_count=0, status=Status.DORMANT)
    unknown = ProjectMetadata(dirty_count=None, status=Status.DORMANT)

    out = render_repos_index(
        [
            _project("clean-repo", metadata=clean),
            _project("unknown-repo", metadata=unknown),
        ],
        generated_at=_NOW,
    )
    clean_row = next(line for line in out.split("\n") if "clean-repo" in line)
    unknown_row = next(line for line in out.split("\n") if "unknown-repo" in line)
    assert "| 0 |" in clean_row
    assert "| 0 |" not in unknown_row
    assert "| — |" in unknown_row


def test_render_escapes_pipes_and_newlines_in_readme() -> None:
    md = ProjectMetadata(
        readme_excerpt="Has a | pipe\nand newline",
        status=Status.ACTIVE,
    )
    out = render_repos_index(
        [_project("tricky", metadata=md)],
        generated_at=_NOW,
    )
    row = next(line for line in out.split("\n") if "tricky" in line)
    # The pipe is escaped (\|) and the newline is collapsed into a space
    assert "Has a \\| pipe and newline" in row
    # The unescaped substring `Has a |` (without the backslash) must NOT
    # appear, otherwise the markdown parser would split the row.
    assert "Has a | pipe" not in row.replace("\\|", "")


def test_render_includes_generated_timestamp() -> None:
    out = render_repos_index([_project("x")], generated_at=_NOW)
    assert "2026-04-11 12:00:00" in out


def test_render_idea_project_is_idea_type() -> None:
    md = ProjectMetadata(status=Status.IN_PROGRESS)
    out = render_repos_index(
        [_project("brain-dump", type=ProjectType.IDEA, metadata=md)],
        generated_at=_NOW,
    )
    row = next(line for line in out.split("\n") if "brain-dump" in line)
    assert "| idea |" in row
    assert "| IN_PROGRESS |" in row


# --- _escape_for_markdown_table ------------------------------------------


def test_escape_strips_pipes_newlines_and_carriage_returns() -> None:
    assert _escape_for_markdown_table("a|b") == "a\\|b"
    assert _escape_for_markdown_table("a\nb") == "a b"
    assert _escape_for_markdown_table("a\r\nb") == "a  b"
    assert _escape_for_markdown_table("  trim  ") == "trim"


# --- write_repos_index round trip ----------------------------------------


def test_write_roundtrip_through_real_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    md = ProjectMetadata(
        branch="trunk",
        dirty_count=2,
        readme_excerpt="Hello",
        status=Status.ACTIVE,
    )
    with Cache(db_path=db_path) as cache:
        cache.upsert([_project("alpha", metadata=md)])
        cache.upsert(
            [
                _project(
                    "beta",
                    type=ProjectType.IDEA,
                    metadata=ProjectMetadata(status=Status.IDEA),
                )
            ]
        )

    output = tmp_path / "out" / "repos-index.md"
    written = write_repos_index(output, db_path=db_path)

    assert written == 2
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "**2** project(s)" in text
    assert "| alpha |" in text
    assert "| beta |" in text
    assert "| ACTIVE |" in text
    assert "| IDEA |" in text


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path) as cache:
        cache.upsert([_project("solo")])

    output = tmp_path / "deep" / "deeper" / "out.md"
    written = write_repos_index(output, db_path=db_path)
    assert written == 1
    assert output.exists()


def test_write_to_empty_cache_still_writes_a_file(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path):
        pass  # creates schema, no rows

    output = tmp_path / "empty.md"
    written = write_repos_index(output, db_path=db_path)
    assert written == 0
    assert output.exists()
    text = output.read_text()
    assert "Cache is empty" in text

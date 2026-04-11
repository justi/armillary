"""SQLite cache unit tests.

Each test uses an isolated `tmp_path` DB so we never touch the user's
real cache directory.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from armillary.cache import SCHEMA_VERSION, Cache, default_db_path
from armillary.models import Project, ProjectType

# --- helpers ----------------------------------------------------------------


def _make_project(
    *,
    path: Path,
    name: str | None = None,
    type: ProjectType = ProjectType.GIT,
    umbrella: Path | None = None,
    last_modified: datetime | None = None,
) -> Project:
    return Project(
        path=path.resolve(),
        name=name or path.name,
        type=type,
        umbrella=(umbrella or path.parent).resolve(),
        last_modified=last_modified or datetime.now(),
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cache.db"


@pytest.fixture
def cache(db_path: Path) -> Cache:
    """A fresh cache bound to a tmp DB. Caller is responsible for closing."""
    c = Cache(db_path=db_path)
    c.open()
    yield c
    c.close()


# --- default_db_path -------------------------------------------------------


def test_default_db_path_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARMILLARY_CACHE_DB", "/tmp/custom-armillary-cache.db")
    assert default_db_path() == Path("/tmp/custom-armillary-cache.db")


def test_default_db_path_expands_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARMILLARY_CACHE_DB", "~/foo/bar.db")
    result = default_db_path()
    assert "~" not in str(result)
    assert result.is_absolute()


def test_default_db_path_falls_back_to_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No override → returns a path under the user's data dir, ending in cache.db."""
    monkeypatch.delenv("ARMILLARY_CACHE_DB", raising=False)
    result = default_db_path()
    assert result.name == "cache.db"
    assert "armillary" in result.parts


# --- schema lifecycle ------------------------------------------------------


def test_open_creates_db_and_schema(db_path: Path) -> None:
    assert not db_path.exists()
    with Cache(db_path=db_path) as cache:
        assert db_path.exists()
        # The projects table is queryable
        assert cache.count() == 0
        version = cache.conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION


def test_reopen_preserves_data(db_path: Path) -> None:
    p = Path("/tmp/some-test-project").resolve()
    with Cache(db_path=db_path) as cache:
        cache.upsert([_make_project(path=p)])
        assert cache.count() == 1

    with Cache(db_path=db_path) as cache:
        assert cache.count() == 1


def test_schema_version_mismatch_triggers_rebuild(db_path: Path) -> None:
    """If the on-disk schema is older than SCHEMA_VERSION, the cache wipes
    and recreates it. M3.2 will rely on this when it bumps the version
    to add metadata columns.
    """
    p = Path("/tmp/old-project").resolve()
    with Cache(db_path=db_path) as cache:
        cache.upsert([_make_project(path=p)])
        assert cache.count() == 1

    # Forcibly downgrade the on-disk version
    raw = sqlite3.connect(db_path)
    raw.execute("PRAGMA user_version = 0")
    raw.commit()
    raw.close()

    with Cache(db_path=db_path) as cache:
        # Old data was wiped on rebuild
        assert cache.count() == 0
        # Version is back to current
        version = cache.conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION


def test_schema_repair_when_table_missing(db_path: Path) -> None:
    """user_version is correct but the table got dropped externally —
    the cache should still recover by recreating the schema.
    """
    with Cache(db_path=db_path) as cache:
        cache.upsert([_make_project(path=Path("/tmp/anything").resolve())])

    raw = sqlite3.connect(db_path)
    raw.execute("DROP TABLE projects")
    raw.commit()
    raw.close()

    with Cache(db_path=db_path) as cache:
        assert cache.count() == 0


def test_conn_property_raises_when_closed(db_path: Path) -> None:
    cache = Cache(db_path=db_path)
    with pytest.raises(RuntimeError, match="not open"):
        _ = cache.conn


# --- upsert ----------------------------------------------------------------


def test_upsert_returns_count_and_persists_rows(cache: Cache, tmp_path: Path) -> None:
    projects = [
        _make_project(path=tmp_path / "alpha"),
        _make_project(path=tmp_path / "beta"),
    ]
    written = cache.upsert(projects)
    assert written == 2
    assert cache.count() == 2


def test_upsert_dedupes_by_path(cache: Cache, tmp_path: Path) -> None:
    """Same path twice → one row, second wins on the mutable fields."""
    p = tmp_path / "thing"
    p.mkdir()

    cache.upsert([_make_project(path=p, type=ProjectType.IDEA, name="old-name")])
    cache.upsert([_make_project(path=p, type=ProjectType.GIT, name="new-name")])

    rows = cache.list_projects()
    assert len(rows) == 1
    assert rows[0].name == "new-name"
    assert rows[0].type is ProjectType.GIT


def test_upsert_stamps_last_scanned_at(cache: Cache, tmp_path: Path) -> None:
    """Each upsert refreshes last_scanned_at; we use this for prune_stale."""
    p = tmp_path / "x"
    cache.upsert([_make_project(path=p)])
    first = cache.conn.execute(
        "SELECT last_scanned_at FROM projects WHERE path = ?", (str(p.resolve()),)
    ).fetchone()[0]

    time.sleep(0.05)
    cache.upsert([_make_project(path=p)])
    second = cache.conn.execute(
        "SELECT last_scanned_at FROM projects WHERE path = ?", (str(p.resolve()),)
    ).fetchone()[0]

    assert second > first


# --- list_projects ---------------------------------------------------------


def test_list_returns_all_when_no_filter(cache: Cache, tmp_path: Path) -> None:
    cache.upsert(
        [
            _make_project(path=tmp_path / "git1", type=ProjectType.GIT),
            _make_project(path=tmp_path / "idea1", type=ProjectType.IDEA),
        ]
    )
    rows = cache.list_projects()
    assert {r.name for r in rows} == {"git1", "idea1"}


def test_list_sorted_by_last_modified_desc(cache: Cache, tmp_path: Path) -> None:
    cache.upsert(
        [
            _make_project(
                path=tmp_path / "old",
                last_modified=datetime(2024, 1, 1),
            ),
            _make_project(
                path=tmp_path / "newer",
                last_modified=datetime(2025, 6, 1),
            ),
            _make_project(
                path=tmp_path / "newest",
                last_modified=datetime(2026, 4, 1),
            ),
        ]
    )
    names = [r.name for r in cache.list_projects()]
    assert names == ["newest", "newer", "old"]


def test_list_filter_by_type(cache: Cache, tmp_path: Path) -> None:
    cache.upsert(
        [
            _make_project(path=tmp_path / "g1", type=ProjectType.GIT),
            _make_project(path=tmp_path / "g2", type=ProjectType.GIT),
            _make_project(path=tmp_path / "i1", type=ProjectType.IDEA),
        ]
    )
    git_only = cache.list_projects(type=ProjectType.GIT)
    idea_only = cache.list_projects(type=ProjectType.IDEA)

    assert {r.name for r in git_only} == {"g1", "g2"}
    assert {r.name for r in idea_only} == {"i1"}


def test_list_filter_by_umbrella_substring(cache: Cache, tmp_path: Path) -> None:
    work = tmp_path / "work"
    play = tmp_path / "play"
    work.mkdir()
    play.mkdir()

    cache.upsert(
        [
            _make_project(path=work / "alpha", umbrella=work),
            _make_project(path=work / "beta", umbrella=work),
            _make_project(path=play / "gamma", umbrella=play),
        ]
    )

    work_only = cache.list_projects(umbrella_substring="work")
    assert {r.name for r in work_only} == {"alpha", "beta"}


def test_list_combines_filters(cache: Cache, tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    cache.upsert(
        [
            _make_project(path=work / "git-thing", umbrella=work, type=ProjectType.GIT),
            _make_project(
                path=work / "idea-thing", umbrella=work, type=ProjectType.IDEA
            ),
        ]
    )
    rows = cache.list_projects(type=ProjectType.GIT, umbrella_substring="work")
    assert [r.name for r in rows] == ["git-thing"]


# --- prune_stale -----------------------------------------------------------


def test_prune_stale_removes_old_rows(cache: Cache, tmp_path: Path) -> None:
    """Anything older than the cutoff is dropped; fresher rows survive."""
    fresh = tmp_path / "fresh"
    cache.upsert([_make_project(path=fresh)])

    # Backdate one row directly via SQL to simulate "scanned long ago"
    stale = tmp_path / "stale"
    cache.upsert([_make_project(path=stale)])
    long_ago = time.time() - 30 * 86400  # 30 days
    cache.conn.execute(
        "UPDATE projects SET last_scanned_at = ? WHERE path = ?",
        (long_ago, str(stale.resolve())),
    )
    cache.conn.commit()

    deleted = cache.prune_stale(older_than=timedelta(days=7))

    assert deleted == 1
    remaining = {r.name for r in cache.list_projects()}
    assert remaining == {"fresh"}


def test_prune_stale_is_a_noop_when_everything_is_fresh(
    cache: Cache, tmp_path: Path
) -> None:
    cache.upsert([_make_project(path=tmp_path / "x")])
    deleted = cache.prune_stale(older_than=timedelta(days=7))
    assert deleted == 0
    assert cache.count() == 1


def test_upsert_basic_only_preserves_existing_metadata(
    cache: Cache, tmp_path: Path
) -> None:
    """Regression for Codex P2: a `--no-metadata` rescan must not wipe
    cached status / branch / dirty_count from an earlier full scan.
    """
    from datetime import datetime as _dt

    from armillary.models import ProjectMetadata, Status

    p = tmp_path / "thing"
    p.mkdir()
    project = _make_project(path=p, type=ProjectType.GIT)
    project.metadata = ProjectMetadata(
        branch="main",
        last_commit_sha="abc1234",
        last_commit_ts=_dt(2025, 6, 1),
        last_commit_author="Someone",
        dirty_count=5,
        readme_excerpt="hello",
        status=Status.PAUSED,
    )

    # First: full upsert with metadata
    cache.upsert([project])
    [row] = cache.list_projects()
    assert row.metadata is not None
    assert row.metadata.status is Status.PAUSED
    assert row.metadata.branch == "main"
    assert row.metadata.dirty_count == 5

    # Now: simulate `armillary scan --no-metadata` — same path, but the
    # in-memory project has no metadata attached.
    bare_project = _make_project(path=p, type=ProjectType.GIT)
    assert bare_project.metadata is None

    cache.upsert([bare_project], write_metadata=False)

    [row] = cache.list_projects()
    assert row.metadata is not None, "metadata must survive a basic-only upsert"
    assert row.metadata.status is Status.PAUSED
    assert row.metadata.branch == "main"
    assert row.metadata.dirty_count == 5
    assert row.metadata.readme_excerpt == "hello"


def test_v2_metadata_json_without_new_fields_still_reads(
    cache: Cache, tmp_path: Path
) -> None:
    """Regression for Codex review on PR #10.

    A row written with the old metadata_json shape (no `ahead`, `behind`,
    `size_bytes`, `file_count`, `note_paths` keys) must still be readable
    after the metadata extraction grew those fields. The new fields just
    come back as None / [] until the next scan refreshes the row.

    This is the test that justifies NOT bumping SCHEMA_VERSION when we
    only add metadata_json keys.
    """
    import json as _json

    p = tmp_path / "legacy"
    p.mkdir()
    # Write directly via SQL using the legacy metadata_json shape
    legacy_json = _json.dumps(
        {
            "last_commit_sha": "abc1234",
            "readme_excerpt": "Old project",
            "adr_paths": ["/tmp/adr/0001.md"],
        }
    )
    cache.conn.execute(
        """
        INSERT INTO projects (
            path, name, type, umbrella,
            last_modified_ts, last_scanned_at,
            status, branch, last_commit_ts, last_commit_author,
            dirty_count, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(p),
            "legacy",
            "git",
            str(p.parent),
            1700000000.0,
            1700000000.0,
            "DORMANT",
            "main",
            1700000000.0,
            "Old Author",
            0,
            legacy_json,
        ),
    )
    cache.conn.commit()

    [row] = cache.list_projects()
    assert row.name == "legacy"
    assert row.metadata is not None
    # Old fields still present
    assert row.metadata.last_commit_sha == "abc1234"
    assert row.metadata.readme_excerpt == "Old project"
    assert len(row.metadata.adr_paths) == 1
    # New fields default to None / []
    assert row.metadata.ahead is None
    assert row.metadata.behind is None
    assert row.metadata.size_bytes is None
    assert row.metadata.file_count is None
    assert row.metadata.note_paths == []


def test_upsert_round_trips_all_v3_metadata_fields(
    cache: Cache, tmp_path: Path
) -> None:
    """Regression for PR #10: every new field added in schema v3
    (ahead, behind, size_bytes, file_count, note_paths) must round-trip
    through cache.upsert → cache.list_projects intact.
    """
    from datetime import datetime as _dt

    from armillary.models import ProjectMetadata, Status

    p = tmp_path / "thing"
    p.mkdir()
    project = _make_project(path=p, type=ProjectType.GIT)
    project.metadata = ProjectMetadata(
        branch="main",
        last_commit_sha="abc1234",
        last_commit_ts=_dt(2025, 6, 1),
        last_commit_author="Someone",
        dirty_count=2,
        ahead=3,
        behind=4,
        size_bytes=12345,
        file_count=42,
        readme_excerpt="Hello",
        adr_paths=[Path("/tmp/adr/0001.md")],
        note_paths=[Path("/tmp/notes/january.md"), Path("/tmp/notes/february.md")],
        status=Status.ACTIVE,
    )

    cache.upsert([project])
    [row] = cache.list_projects()

    assert row.metadata is not None
    assert row.metadata.ahead == 3
    assert row.metadata.behind == 4
    assert row.metadata.size_bytes == 12345
    assert row.metadata.file_count == 42
    assert {p.name for p in row.metadata.note_paths} == {
        "january.md",
        "february.md",
    }


def test_upsert_basic_only_inserts_new_rows_with_null_metadata(
    cache: Cache, tmp_path: Path
) -> None:
    """A first-ever scan with `--no-metadata` must still insert the row,
    just with metadata columns left NULL for the next full scan to fill.
    """
    project = _make_project(path=tmp_path / "fresh", type=ProjectType.GIT)
    cache.upsert([project], write_metadata=False)

    [row] = cache.list_projects()
    assert row.name == "fresh"
    assert row.metadata is None  # truly nothing extracted yet


def test_clear_projects_wipes_every_row(cache: Cache, tmp_path: Path) -> None:
    """`Cache.clear_projects()` deletes every row regardless of age.

    Used by `armillary config --init` to start from a clean slate so
    rows from a previous umbrella selection do not linger in the
    dashboard until the (default 7-day) prune cutoff catches them.
    """
    cache.upsert(
        [
            _make_project(path=tmp_path / "alpha"),
            _make_project(path=tmp_path / "beta"),
            _make_project(path=tmp_path / "gamma"),
        ]
    )
    assert cache.count() == 3

    deleted = cache.clear_projects()

    assert deleted == 3
    assert cache.count() == 0


def test_clear_projects_on_empty_table_returns_zero(cache: Cache) -> None:
    """No-op when the table is already empty — must not raise."""
    assert cache.count() == 0
    assert cache.clear_projects() == 0
    assert cache.count() == 0


def test_prune_stale_does_not_touch_unrelated_umbrellas(
    cache: Cache, tmp_path: Path
) -> None:
    """Multi-umbrella scans must not nuke each other.

    Scanning umbrella A then umbrella B refreshes only B's rows. A's rows
    keep their previous last_scanned_at and only fall off prune_stale's
    cutoff naturally with time, never as a side effect of scanning B.
    """
    a = tmp_path / "A"
    b = tmp_path / "B"
    a.mkdir()
    b.mkdir()

    cache.upsert([_make_project(path=a / "alpha", umbrella=a)])
    time.sleep(0.05)
    cache.upsert([_make_project(path=b / "beta", umbrella=b)])

    # Default cutoff is 7 days; both rows are far younger
    deleted = cache.prune_stale()
    assert deleted == 0
    assert {r.name for r in cache.list_projects()} == {"alpha", "beta"}

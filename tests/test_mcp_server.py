"""Tests for `armillary.mcp_server` — MCP tool functions.

Tests the pure logic: project context enrichment, hit-to-dict conversion,
and the armillary_projects tool output shape. Search tools are integration-
level (need ripgrep + real repos) so we only test the helpers here.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from armillary.cache import Cache
from armillary.mcp_server import _hit_to_dict, _project_context, armillary_projects
from armillary.models import Project, ProjectMetadata, ProjectType, Status
from armillary.search import SearchHit

_NOW = datetime(2026, 4, 12, 12, 0, 0)


def _project(
    name: str,
    *,
    metadata: ProjectMetadata | None = None,
    path: Path | None = None,
) -> Project:
    base = path or Path(f"/tmp/{name}")
    return Project(
        path=base,
        name=name,
        type=ProjectType.GIT,
        umbrella=base.parent,
        last_modified=_NOW,
        metadata=metadata,
    )


# --- _project_context ------------------------------------------------------


def test_project_context_returns_nulls_for_missing_project(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path):
        pass  # empty cache

    # Patch default db_path — _project_context uses Cache() without args,
    # so we test it indirectly via armillary_projects which also uses Cache().
    result = _project_context("nonexistent")
    assert result["path"] is None
    assert result["status"] is None
    assert result["description"] is None


def test_project_context_returns_metadata_for_existing_project(
    tmp_path: Path, monkeypatch: object
) -> None:
    db_path = tmp_path / "cache.db"
    md = ProjectMetadata(
        status=Status.ACTIVE,
        readme_excerpt="A quiz app.",
    )
    with Cache(db_path=db_path) as cache:
        cache.upsert([_project("quiz", metadata=md)])

    # Monkey-patch Cache to use our test db
    import armillary.mcp_server as mcp_mod

    original_cache = Cache.__init__

    def patched_init(self: Cache, *, db_path: Path | None = None) -> None:
        original_cache(self, db_path=tmp_path / "cache.db")

    monkeypatch.setattr(Cache, "__init__", patched_init)  # type: ignore[arg-type]

    result = mcp_mod._project_context("quiz")
    assert result["path"] == "/tmp/quiz"
    assert result["status"] == "ACTIVE"
    assert result["description"] == "A quiz app."


# --- _hit_to_dict -----------------------------------------------------------


def test_hit_to_dict_merges_meta_and_hit() -> None:
    meta = {"path": "/tmp/foo", "status": "ACTIVE", "description": "Foo app"}
    hit = SearchHit(
        path=Path("/tmp/foo/bar.py"), line=42, preview="def bar():", backend="ripgrep"
    )

    result = _hit_to_dict(hit, meta)

    assert result["path"] == "/tmp/foo"
    assert result["status"] == "ACTIVE"
    assert result["file"] == "/tmp/foo/bar.py"
    assert result["line"] == 42
    assert result["preview"] == "def bar():"


# --- armillary_projects -----------------------------------------------------


def test_armillary_projects_returns_all(tmp_path: Path, monkeypatch: object) -> None:
    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path) as cache:
        cache.upsert(
            [
                _project(
                    "alpha",
                    metadata=ProjectMetadata(
                        status=Status.ACTIVE, readme_excerpt="Alpha app"
                    ),
                ),
                _project("beta", metadata=ProjectMetadata(status=Status.DORMANT)),
            ]
        )

    original_cache = Cache.__init__

    def patched_init(self: Cache, *, db_path: Path | None = None) -> None:
        original_cache(self, db_path=tmp_path / "cache.db")

    monkeypatch.setattr(Cache, "__init__", patched_init)  # type: ignore[arg-type]

    result = json.loads(armillary_projects())
    assert len(result) == 2
    paths = {r["path"] for r in result}
    assert "/tmp/alpha" in paths
    assert "/tmp/beta" in paths
    # Check shape
    for row in result:
        assert "path" in row
        assert "status" in row
        assert "description" in row


def test_armillary_projects_filters_by_status(
    tmp_path: Path, monkeypatch: object
) -> None:
    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path) as cache:
        cache.upsert(
            [
                _project("active1", metadata=ProjectMetadata(status=Status.ACTIVE)),
                _project("dormant1", metadata=ProjectMetadata(status=Status.DORMANT)),
                _project("active2", metadata=ProjectMetadata(status=Status.ACTIVE)),
            ]
        )

    original_cache = Cache.__init__

    def patched_init(self: Cache, *, db_path: Path | None = None) -> None:
        original_cache(self, db_path=tmp_path / "cache.db")

    monkeypatch.setattr(Cache, "__init__", patched_init)  # type: ignore[arg-type]

    result = json.loads(armillary_projects(status_filter="ACTIVE"))
    assert len(result) == 2
    assert all(r["status"] == "ACTIVE" for r in result)


def test_armillary_projects_empty_cache(tmp_path: Path, monkeypatch: object) -> None:
    db_path = tmp_path / "cache.db"
    with Cache(db_path=db_path):
        pass

    original_cache = Cache.__init__

    def patched_init(self: Cache, *, db_path: Path | None = None) -> None:
        original_cache(self, db_path=tmp_path / "cache.db")

    monkeypatch.setattr(Cache, "__init__", patched_init)  # type: ignore[arg-type]

    result = json.loads(armillary_projects())
    assert result == []

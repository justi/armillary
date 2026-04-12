"""Tests for `armillary.mcp_server` — MCP tool functions.

Tests the pure logic: project context enrichment, hit-to-dict conversion,
armillary_projects tool output, and response size safety guards.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from armillary.cache import Cache
from armillary.mcp_server import (
    _PREVIEW_MAX_LEN,
    _RESPONSE_MAX_CHARS,
    _clamp_max_results,
    _hit_to_dict,
    _project_context,
    _safe_json,
    armillary_projects,
    armillary_search,
)
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


@pytest.fixture()
def _use_tmp_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Route all Cache() calls to a temp DB via env var."""
    db_path = tmp_path / "cache.db"
    monkeypatch.setenv("ARMILLARY_CACHE_DB", str(db_path))
    return db_path


# --- _project_context ------------------------------------------------------


def test_project_context_returns_nulls_for_missing_project(
    _use_tmp_cache: Path,
) -> None:
    db_path = _use_tmp_cache
    with Cache(db_path=db_path):
        pass  # empty cache

    result = _project_context("nonexistent")
    assert result["path"] is None
    assert result["status"] is None
    assert result["description"] is None


def test_project_context_returns_metadata_for_existing_project(
    _use_tmp_cache: Path,
) -> None:
    db_path = _use_tmp_cache
    md = ProjectMetadata(
        status=Status.ACTIVE,
        readme_excerpt="A quiz app.",
    )
    with Cache(db_path=db_path) as cache:
        cache.upsert([_project("quiz", metadata=md)])

    result = _project_context("quiz")
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


def test_hit_to_dict_truncates_long_preview() -> None:
    long_preview = "x" * 300
    meta = {"path": "/tmp/foo", "status": "ACTIVE", "description": None}
    hit = SearchHit(
        path=Path("/tmp/foo/bar.py"),
        line=1,
        preview=long_preview,
        backend="ripgrep",
    )
    result = _hit_to_dict(hit, meta)
    assert len(result["preview"]) == _PREVIEW_MAX_LEN + 1  # +1 for "…"
    assert result["preview"].endswith("…")


def test_hit_to_dict_keeps_short_preview() -> None:
    meta = {"path": "/tmp/foo", "status": "ACTIVE", "description": None}
    hit = SearchHit(
        path=Path("/tmp/foo/bar.py"),
        line=1,
        preview="short",
        backend="ripgrep",
    )
    result = _hit_to_dict(hit, meta)
    assert result["preview"] == "short"


# --- armillary_projects -----------------------------------------------------


def test_armillary_projects_returns_all(_use_tmp_cache: Path) -> None:
    db_path = _use_tmp_cache
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

    result = json.loads(armillary_projects())
    assert len(result) == 2
    paths = {r["path"] for r in result}
    assert "/tmp/alpha" in paths
    assert "/tmp/beta" in paths
    for row in result:
        assert "path" in row
        assert "status" in row
        assert "description" in row


def test_armillary_projects_filters_by_status(_use_tmp_cache: Path) -> None:
    db_path = _use_tmp_cache
    with Cache(db_path=db_path) as cache:
        cache.upsert(
            [
                _project("active1", metadata=ProjectMetadata(status=Status.ACTIVE)),
                _project("dormant1", metadata=ProjectMetadata(status=Status.DORMANT)),
                _project("active2", metadata=ProjectMetadata(status=Status.ACTIVE)),
            ]
        )

    result = json.loads(armillary_projects(status_filter="ACTIVE"))
    assert len(result) == 2
    assert all(r["status"] == "ACTIVE" for r in result)


def test_armillary_projects_empty_cache(_use_tmp_cache: Path) -> None:
    db_path = _use_tmp_cache
    with Cache(db_path=db_path):
        pass

    result = json.loads(armillary_projects())
    assert result == []


# --- _safe_json truncation --------------------------------------------------


def test_safe_json_returns_compact_json() -> None:
    results = [{"a": 1}, {"a": 2}]
    output = _safe_json(results, 2, 2)
    assert " " not in output
    parsed = json.loads(output)
    assert len(parsed) == 2


def test_safe_json_adds_truncated_marker_when_shown_less_than_total() -> None:
    results = [{"a": 1}]
    output = _safe_json(results, 5, 1)
    parsed = json.loads(output)
    assert len(parsed) == 2
    assert parsed[-1]["_truncated"] == 4


def test_safe_json_trims_results_when_over_char_limit() -> None:
    big_results = [{"data": "x" * 500, "i": i} for i in range(200)]
    output = _safe_json(big_results, 200, 200)
    assert len(output) <= _RESPONSE_MAX_CHARS + 100
    parsed = json.loads(output)
    assert parsed[-1]["_truncated"] > 0


def test_safe_json_no_marker_when_all_shown() -> None:
    results = [{"a": 1}]
    output = _safe_json(results, 1, 1)
    parsed = json.loads(output)
    assert len(parsed) == 1
    assert "_truncated" not in parsed[0]


def test_clamp_max_results_enforces_public_bounds() -> None:
    assert _clamp_max_results(-5) == 1
    assert _clamp_max_results(0) == 1
    assert _clamp_max_results(1) == 1
    assert _clamp_max_results(999) == 200


def test_armillary_search_clamps_zero_max_results_before_backend_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    class FakeLiteralSearch:
        def search(
            self, query: str, *, root: Path, max_results: int = 50
        ) -> list[SearchHit]:
            calls.append(max_results)
            return []

    monkeypatch.setattr("armillary.mcp_server.LiteralSearch", FakeLiteralSearch)
    monkeypatch.setattr(
        "armillary.mcp_server._get_project_roots",
        lambda: [("alpha", Path("/tmp/alpha"))],
    )

    result = armillary_search("needle", max_results=0)

    assert calls == [1]
    assert result == "No matches for 'needle' across 1 projects."

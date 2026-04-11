"""Search backend tests.

`LiteralSearch` runs against real `ripgrep` if it is on PATH (skipped
otherwise — never installed silently in tests). `KhojSearch` is fully
mocked: we never make network calls, only verify the URL construction,
JSON parsing, and the ripgrep fallback path.
"""

from __future__ import annotations

import json
import shutil
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from urllib.error import URLError

import pytest

from armillary import search
from armillary.search import (
    KhojConfig,
    KhojSearch,
    LiteralSearch,
    SearchHit,
    _parse_khoj_response,
    _parse_ripgrep_jsonl,
)

_HAVE_RG = shutil.which("rg") is not None


# --- LiteralSearch / ripgrep ---------------------------------------------


@pytest.mark.skipif(not _HAVE_RG, reason="ripgrep not installed")
def test_literal_search_finds_matches_in_real_files(tmp_path: Path) -> None:
    (tmp_path / "alpha.py").write_text("def needle():\n    return 1\n")
    (tmp_path / "beta.txt").write_text("nothing useful here\n")

    backend = LiteralSearch()
    hits = backend.search("needle", root=tmp_path)

    assert len(hits) == 1
    assert hits[0].path.name == "alpha.py"
    assert hits[0].line == 1
    assert "needle" in hits[0].preview
    assert hits[0].backend == "ripgrep"


@pytest.mark.skipif(not _HAVE_RG, reason="ripgrep not installed")
def test_literal_search_respects_max_results(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("needle\n")
    backend = LiteralSearch()
    hits = backend.search("needle", root=tmp_path, max_results=3)
    assert len(hits) == 3


@pytest.mark.skipif(not _HAVE_RG, reason="ripgrep not installed")
def test_literal_search_returns_empty_for_blank_query(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("hello\n")
    backend = LiteralSearch()
    assert backend.search("", root=tmp_path) == []
    assert backend.search("   ", root=tmp_path) == []


def test_literal_search_unavailable_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `rg` is not on PATH, `LiteralSearch` returns no hits instead of
    crashing — the CLI layer is responsible for telling the user."""
    monkeypatch.setattr(search.shutil, "which", lambda name: None)
    backend = LiteralSearch()
    assert backend.is_available() is False
    assert backend.search("anything", root=tmp_path) == []


def test_parse_ripgrep_jsonl_handles_real_event_format() -> None:
    """A small slice of real `rg --json` output."""
    raw = "\n".join(
        [
            json.dumps(
                {
                    "type": "match",
                    "data": {
                        "path": {"text": "/tmp/foo/bar.py"},
                        "lines": {"text": "    def needle(self):\n"},
                        "line_number": 42,
                    },
                }
            ),
            json.dumps({"type": "begin", "data": {}}),  # ignored
            json.dumps(
                {
                    "type": "match",
                    "data": {
                        "path": {"text": "/tmp/foo/baz.py"},
                        "lines": {"text": "needle in baz\n"},
                        "line_number": 7,
                    },
                }
            ),
        ]
    )
    hits = _parse_ripgrep_jsonl(raw, max_results=10)
    assert len(hits) == 2
    assert hits[0].path == Path("/tmp/foo/bar.py")
    assert hits[0].line == 42
    assert "needle" in hits[0].preview
    assert hits[0].backend == "ripgrep"


def test_parse_ripgrep_jsonl_skips_malformed_lines() -> None:
    raw = "{\nthis is garbage\n" + json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"text": "/tmp/x.py"},
                "lines": {"text": "ok\n"},
                "line_number": 1,
            },
        }
    )
    hits = _parse_ripgrep_jsonl(raw, max_results=10)
    assert len(hits) == 1
    assert hits[0].path == Path("/tmp/x.py")


# --- KhojSearch -----------------------------------------------------------


def _fake_urlopen(payload: object) -> Any:
    """Build a fake `urlopen` context manager that returns `payload` as JSON."""

    body = json.dumps(payload).encode("utf-8")

    class FakeResponse:
        def __init__(self) -> None:
            self._buf = BytesIO(body)

        def read(self) -> bytes:
            return self._buf.read()

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    def opener(request: Any, **kwargs: Any) -> FakeResponse:
        opener.captured_url = request.full_url
        opener.captured_headers = dict(request.headers)
        return FakeResponse()

    opener.captured_url = None
    opener.captured_headers = None
    return opener


def test_khoj_search_constructs_url_and_parses_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Paths must be under `root` (tmp_path) so the post-filter keeps them.
    payload = [
        {
            "entry": "Some semantic snippet",
            "additional": {"file": str(tmp_path / "notes" / "idea.md")},
        },
        {
            "entry": "Another match",
            "additional": {"file": str(tmp_path / "notes" / "other.md")},
        },
    ]
    opener = _fake_urlopen(payload)
    monkeypatch.setattr(search, "urlopen", opener)

    backend = KhojSearch(KhojConfig(api_url="http://localhost:42110", api_key=None))
    hits = backend.search("idea", root=tmp_path)

    assert len(hits) == 2
    assert hits[0].path.name == "idea.md"
    assert hits[0].backend == "khoj"
    assert hits[0].line is None
    assert "snippet" in hits[0].preview.lower()

    # URL was assembled correctly
    assert "http://localhost:42110/api/search?" in opener.captured_url
    assert "q=idea" in opener.captured_url
    # No bearer when api_key is None
    assert "Authorization" not in opener.captured_headers


def test_khoj_search_passes_bearer_token_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    opener = _fake_urlopen([])
    monkeypatch.setattr(search, "urlopen", opener)

    backend = KhojSearch(KhojConfig(api_url="http://x", api_key="secret"))
    backend.search("anything", root=tmp_path)

    # Header capitalisation varies, normalize for the assert
    assert any(v == "Bearer secret" for v in opener.captured_headers.values())


def test_khoj_search_falls_back_to_ripgrep_on_url_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Khoj down → fall back to literal search rather than crashing."""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise URLError("connection refused")

    monkeypatch.setattr(search, "urlopen", boom)

    fallback = MagicMock()
    fallback.search.return_value = [
        SearchHit(path=Path("/tmp/x"), line=1, preview="found", backend="ripgrep")
    ]

    backend = KhojSearch(fallback=fallback)
    hits = backend.search("query", root=tmp_path)

    assert len(hits) == 1
    assert hits[0].backend == "ripgrep"
    fallback.search.assert_called_once()


def test_khoj_search_falls_back_on_malformed_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeResp:
        def read(self) -> bytes:
            return b"not json"

        def __enter__(self) -> FakeResp:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr(search, "urlopen", lambda *a, **k: FakeResp())

    fallback = MagicMock()
    fallback.search.return_value = []
    backend = KhojSearch(fallback=fallback)
    backend.search("query", root=tmp_path)
    fallback.search.assert_called_once()


def test_khoj_search_returns_empty_for_blank_query(tmp_path: Path) -> None:
    backend = KhojSearch()
    assert backend.search("", root=tmp_path) == []


def test_parse_khoj_response_skips_invalid_items() -> None:
    payload = [
        {"entry": "ok", "additional": {"file": "/tmp/a.md"}},
        "string instead of dict",  # ignored
        {"entry": "no path"},  # missing additional.file → ignored
        {"additional": {"file": "/tmp/b.md"}, "entry": ["wrong type"]},  # ignored
        {"entry": "good", "additional": {"file": "/tmp/c.md"}},
    ]
    hits = _parse_khoj_response(payload, max_results=10)
    assert {h.path.name for h in hits} == {"a.md", "c.md"}


def test_parse_khoj_response_raises_on_non_list_payload() -> None:
    """Regression for Codex review P2: a JSON object with the wrong
    top-level shape (auth wrapper, error envelope, future API version)
    must raise so the caller can fall back instead of silently
    returning [] which looks like "no matches".
    """
    from armillary.search import KhojResponseError

    with pytest.raises(KhojResponseError):
        _parse_khoj_response({"unexpected": "shape"}, max_results=10)
    with pytest.raises(KhojResponseError):
        _parse_khoj_response(None, max_results=10)
    with pytest.raises(KhojResponseError):
        _parse_khoj_response("just a string", max_results=10)


# --- Codex round 2 regressions --------------------------------------------


def test_khoj_search_post_filters_by_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression for Codex review P1: Khoj returns global hits, the
    backend must restrict each `search(root=...)` call to its own root."""
    project_a = tmp_path / "alpha"
    project_b = tmp_path / "beta"
    project_a.mkdir()
    project_b.mkdir()

    payload = [
        {
            "entry": "match in alpha",
            "additional": {"file": str(project_a / "x.md")},
        },
        {
            "entry": "match in beta",
            "additional": {"file": str(project_b / "y.md")},
        },
        {
            "entry": "outside any project",
            "additional": {"file": str(tmp_path / "loose.md")},
        },
    ]
    opener = _fake_urlopen(payload)
    monkeypatch.setattr(search, "urlopen", opener)

    backend = KhojSearch(KhojConfig(api_url="http://x"))

    a_hits = backend.search("match", root=project_a)
    b_hits = backend.search("match", root=project_b)

    assert {h.path.name for h in a_hits} == {"x.md"}
    assert {h.path.name for h in b_hits} == {"y.md"}


def test_khoj_search_caches_global_query_across_iterations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Successive `search()` calls for the same query+max_results re-use
    the cached global response so we hit Khoj once, not N times."""
    payload = [
        {"entry": "x", "additional": {"file": str(tmp_path / "a.md")}},
    ]
    call_count = {"n": 0}

    def counting_opener(request: Any, **kwargs: Any) -> Any:
        call_count["n"] += 1
        return _fake_urlopen(payload)(request, **kwargs)

    monkeypatch.setattr(search, "urlopen", counting_opener)

    backend = KhojSearch(KhojConfig(api_url="http://x"))
    backend.search("query", root=tmp_path)
    backend.search("query", root=tmp_path)
    backend.search("query", root=tmp_path)

    assert call_count["n"] == 1


def test_khoj_search_falls_back_on_unexpected_payload_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression for Codex review P2: an `{"error": ...}` wrapper or
    nested envelope must trigger the ripgrep fallback rather than
    looking like a successful no-hit search."""
    opener = _fake_urlopen({"error": "auth required"})
    monkeypatch.setattr(search, "urlopen", opener)

    fallback = MagicMock()
    fallback.search.return_value = [
        SearchHit(path=Path("/tmp/x"), line=1, preview="found", backend="ripgrep")
    ]

    backend = KhojSearch(fallback=fallback)
    hits = backend.search("query", root=tmp_path)

    assert len(hits) == 1
    assert hits[0].backend == "ripgrep"
    fallback.search.assert_called_once()


def test_khoj_search_without_fallback_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression for Codex review P3: with no fallback configured (e.g.
    on a machine without ripgrep), Khoj failures must propagate so the
    CLI can show a clear error instead of returning [] silently."""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise URLError("connection refused")

    monkeypatch.setattr(search, "urlopen", boom)

    backend = KhojSearch(fallback=None)
    with pytest.raises(URLError):
        backend.search("query", root=tmp_path)

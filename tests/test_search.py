"""Search backend tests.

`LiteralSearch` runs against real `ripgrep` if it is on PATH (skipped
otherwise — never installed silently in tests).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from armillary import search
from armillary.search import (
    LiteralSearch,
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


def test_literal_search_runs_rg_with_expected_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    stdout = json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"text": str(tmp_path / "alpha.py")},
                "lines": {"text": "needle()\n"},
                "line_number": 3,
            },
        }
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(stdout=stdout, returncode=0)

    monkeypatch.setattr(search.shutil, "which", lambda name: "/usr/bin/rg")
    monkeypatch.setattr(search.subprocess, "run", fake_run)

    hits = LiteralSearch().search("needle", root=tmp_path, max_results=7)

    assert captured["cmd"] == [
        "rg",
        "--json",
        "--max-count",
        "7",
        "--max-filesize",
        "1M",
        "--",
        "needle",
        str(tmp_path),
    ]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["timeout"] == 15
    assert len(hits) == 1
    assert hits[0].path == tmp_path / "alpha.py"
    assert hits[0].line == 3


def test_literal_search_returns_empty_on_subprocess_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(search.shutil, "which", lambda name: "/usr/bin/rg")

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        raise search.subprocess.TimeoutExpired(cmd=["rg"], timeout=15)

    monkeypatch.setattr(search.subprocess, "run", fake_run)

    assert LiteralSearch().search("needle", root=tmp_path) == []


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

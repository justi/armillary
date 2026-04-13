"""Search backend — ripgrep literal search across all indexed repos.

Shells out to `rg` (ripgrep) and parses JSON output. Returns a list
of `SearchHit` with file path, line number, and the matched line.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SearchHit:
    """A single match across all indexed projects.

    `path` is the absolute file the match came from. `line` is the
    1-based line number (None when unavailable). `preview` is the
    matched line — kept short enough to render in a table cell.
    """

    path: Path
    line: int | None
    preview: str
    backend: str


class SearchBackend(Protocol):
    def search(
        self, query: str, *, root: Path, max_results: int = 50
    ) -> list[SearchHit]: ...


# --- ripgrep ---------------------------------------------------------------


class LiteralSearch:
    """`ripgrep` wrapper. The default search backend.

    Returns up to `max_results` matches across the project tree under
    `root`. Hidden files / .git / .venv are skipped via ripgrep's own
    `--hidden=false` default plus its built-in ignore-file handling.
    """

    name = "ripgrep"

    @staticmethod
    def is_available() -> bool:
        return shutil.which("rg") is not None

    def search(
        self,
        query: str,
        *,
        root: Path,
        max_results: int = 50,
    ) -> list[SearchHit]:
        if not query.strip():
            return []
        if not self.is_available():
            return []
        cmd = [
            "rg",
            "--json",
            "--max-count",
            str(max_results),
            "--max-filesize",
            "1M",
            "--",
            query,
            str(root),
        ]
        try:
            proc = subprocess.run(  # noqa: S603 — args list, no shell, query is the only user input
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (subprocess.TimeoutExpired, OSError):
            return []
        return _parse_ripgrep_jsonl(proc.stdout, max_results=max_results)


def _parse_ripgrep_jsonl(stdout: str, *, max_results: int) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for raw_line in stdout.splitlines():
        if not raw_line:
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data", {})
        path_obj = data.get("path", {})
        path_str = path_obj.get("text") or path_obj.get("bytes")
        if not path_str:
            continue
        line_no = data.get("line_number")
        lines = data.get("lines", {})
        preview = (lines.get("text") or "").strip()
        hits.append(
            SearchHit(
                path=Path(path_str),
                line=line_no,
                preview=preview[:200],
                backend="ripgrep",
            )
        )
        if len(hits) >= max_results:
            break
    return hits

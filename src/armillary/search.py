"""Search backends — ripgrep (always available) + Khoj (optional).

Two backends with the same `search()` shape so the dashboard / CLI
can switch between them based on config without `if khoj_enabled` at
every call site:

- `LiteralSearch` shells out to `ripgrep` (the user must have `rg` on
  PATH). Returns a list of `SearchHit` with file path + line number +
  the matched line.
- `KhojSearch` calls the Khoj REST API at the configured URL. Same
  return type, but the "line number" is None and "preview" is the
  semantic snippet from Khoj. **Optional**: if Khoj is unreachable
  (connection refused, timeout, non-2xx response), the search falls
  back to `LiteralSearch` so the dashboard never shows a broken state.

Per PLAN.md §11: "Khoj reliability — if Khoj is unreachable, fall
back to literal search (`ripgrep`)." That fallback is implemented
inside `KhojSearch.search()` itself, so callers do not need to know
which backend they got.

This module deliberately uses `urllib` from the standard library
rather than adding `httpx` to the dependency tree — Khoj's REST API
is small, the requests are JSON-in / JSON-out, and a 30-line stdlib
client beats a new dependency.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SearchHit:
    """A single match across all indexed projects.

    `path` is the absolute file the match came from. `line` is the
    1-based line number for ripgrep matches and `None` for Khoj
    semantic hits. `preview` is the matched line (ripgrep) or the
    semantic snippet (Khoj) — kept short enough to render in a
    dashboard table cell without wrapping aggressively.
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


# --- Khoj -----------------------------------------------------------------


@dataclass(frozen=True)
class KhojConfig:
    api_url: str = "http://localhost:42110"
    api_key: str | None = None
    timeout_seconds: float = 5.0


class KhojResponseError(Exception):
    """Raised when Khoj returns a payload we cannot interpret as a result list.

    Examples: an `{"error": ...}` wrapper, an empty `null`, a string,
    or a different API version's nested envelope. Caught by
    `KhojSearch.search()` and treated the same as a network failure
    so the fallback fires instead of silently dropping all results.
    """


class KhojSearch:
    """Khoj REST API client with optional ripgrep fallback and per-query caching.

    Khoj exposes a `/api/search?q=...` endpoint that returns JSON. The
    response is **global** — there is no per-project filter — so this
    backend caches the global result per `(query, max_results)` and
    post-filters each `search(root=...)` call to the hits whose path
    is under that root. Without this, scanning N projects would query
    Khoj N times and print the same global hits under every project.

    Fallback semantics:
    - If `fallback` is set (e.g. `LiteralSearch()`), any Khoj failure
      transparently delegates to the fallback for the SAME query.
    - If `fallback` is `None`, Khoj failures raise an exception so the
      caller can surface the error. Use this when ripgrep is not
      available — we never want a "no matches" silent fallthrough.
    """

    name = "khoj"

    def __init__(
        self,
        config: KhojConfig | None = None,
        *,
        fallback: SearchBackend | None = None,
    ) -> None:
        self.config = config or KhojConfig()
        self.fallback = fallback
        # Per-instance cache keyed by (query, max_results). Each entry is
        # either a list of hits (success) or the sentinel "fallback" so
        # we re-use the fallback decision across iterations rather than
        # re-hitting Khoj N times for the same broken query.
        self._cache: dict[tuple[str, int], list[SearchHit] | str] = {}

    def search(
        self,
        query: str,
        *,
        root: Path,
        max_results: int = 50,
    ) -> list[SearchHit]:
        if not query.strip():
            return []

        cache_key = (query, max_results)
        cached = self._cache.get(cache_key)

        if cached is None:
            try:
                hits = self._search_khoj(query, max_results=max_results)
            except (
                HTTPError,
                URLError,
                json.JSONDecodeError,
                TimeoutError,
                OSError,
                KhojResponseError,
            ):
                if self.fallback is None:
                    # No fallback configured — bubble up so the CLI can
                    # tell the user that Khoj is broken AND ripgrep is
                    # missing, instead of silently returning [].
                    raise
                self._cache[cache_key] = "fallback"
                cached = "fallback"
            else:
                self._cache[cache_key] = hits
                cached = hits

        if cached == "fallback":
            assert self.fallback is not None
            return self.fallback.search(query, root=root, max_results=max_results)

        # Successful Khoj cache — post-filter to the hits under `root`
        # so each project iteration gets only its own results.
        return [h for h in cached if _is_under(h.path, root)][:max_results]

    def _search_khoj(self, query: str, *, max_results: int) -> list[SearchHit]:
        params = urlencode({"q": query, "n": max_results})
        url = f"{self.config.api_url.rstrip('/')}/api/search?{params}"
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        request = Request(url, headers=headers)
        with urlopen(request, timeout=self.config.timeout_seconds) as response:  # noqa: S310 — http(s) only, URL from config
            payload = json.loads(response.read().decode("utf-8"))
        return _parse_khoj_response(payload, max_results=max_results)


def _is_under(path: Path, root: Path) -> bool:
    """True if `path` is `root` itself or any descendant of `root`."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _parse_khoj_response(payload: object, *, max_results: int) -> list[SearchHit]:
    """Parse Khoj's `/api/search` response into a list of hits.

    Khoj has changed its API shape a few times, so we are tolerant about
    the *contents* of each item and skip ones we cannot decode. But the
    top-level shape MUST be a list — anything else (an error wrapper, a
    nested envelope from a future API version, ...) raises
    `KhojResponseError` so the caller can fall back to ripgrep instead
    of silently dropping every result.
    """
    if not isinstance(payload, list):
        raise KhojResponseError(
            f"Expected a JSON list from Khoj, got {type(payload).__name__}"
        )
    hits: list[SearchHit] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        additional = item.get("additional") or {}
        file_path = additional.get("file") if isinstance(additional, dict) else None
        if not file_path:
            continue
        snippet = item.get("entry") or item.get("compiled") or ""
        if not isinstance(snippet, str):
            continue
        hits.append(
            SearchHit(
                path=Path(file_path),
                line=None,
                preview=snippet.replace("\n", " ").strip()[:200],
                backend="khoj",
            )
        )
        if len(hits) >= max_results:
            break
    return hits

"""Walk a git repo, cut source files into 40-line windows (ADR 0027).

Each window gets a best-effort symbol heuristic by regex-matching the
first non-blank line. No AST parsing — that was explicitly cut from
v1 because it forces language-specific dependencies. This module is
language-agnostic; an empty symbol is fine.

Public API: :func:`build_blocks_for_repo` returns a list of
:class:`CodeBlock`. Caller (scan_service) feeds them to
:class:`code_index.CodeIndex`.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .framework_profiles import Profile

_WINDOW_SIZE = 40
_WINDOW_STRIDE = 20
_MAX_FILE_BYTES = 500 * 1024
_BINARY_SNIFF_BYTES = 8 * 1024
_GIT_LS_TIMEOUT_SECONDS = 30

_SKIP_SUFFIXES: frozenset[str] = frozenset(
    {
        ".lock",
        ".map",
        ".svg",
        ".ico",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp3",
        ".mp4",
        ".mov",
        ".wav",
    }
)

_SKIP_DOUBLE_SUFFIXES: tuple[str, ...] = (
    ".min.js",
    ".min.css",
    ".d.ts",
)

_SKIP_PATH_SEGMENTS: frozenset[str] = frozenset(
    {
        "node_modules",
        "vendor",
        "dist",
        "build",
        ".venv",
        "venv",
        "__pycache__",
        ".next",
        ".nuxt",
        ".cache",
        "target",  # rust/maven builds
        ".mypy_cache",
        ".pytest_cache",
    }
)

_SYMBOL_RE = re.compile(
    r"^\s*(?:"
    r"async\s+def|def|function|class|func|fn|sub|"
    r"export\s+(?:async\s+)?function|"
    r"export\s+(?:default\s+)?class|"
    r"public\s+class|"
    r"public\s+static\s+\w+"
    r")\s+(\w+)"
)


@dataclass(frozen=True)
class RepoIndexResult:
    """Profile + counts after walking a repo (ADR 0031 observability).

    Returned alongside the block list so :mod:`scan_service` can
    persist ``files_indexed`` / ``files_skipped`` on the project
    metadata blob — that's the regression guard against a profile
    that detects a custom Rails layout and silently indexes nothing.
    """

    profile_name: str
    files_indexed: int
    files_skipped: int


@dataclass(frozen=True)
class CodeBlock:
    """A 40-line window from a source file, plus a heuristic symbol.

    ``path`` is absolute. ``symbol`` is the first identifier captured
    from the block's first non-blank line (``None`` when no known
    declaration keyword matches). ``updated_at`` is the file's mtime at
    extraction time.
    """

    repo_path: str
    path: str
    language_ext: str
    start_line: int
    end_line: int
    content: str
    symbol: str | None
    updated_at: float


def build_blocks_for_repo(
    repo_path: Path,
    *,
    profile: Profile | None = None,
) -> list[CodeBlock]:
    """Return every 40-line window across tracked files in a repo.

    When ``profile`` is set (ADR 0031), only files whose path is
    accepted by ``profile.accepts(rel)`` reach window extraction —
    everything else (migrations, generated assets, fixtures) is
    filtered out before it can dilute Steal's index. ``profile=None``
    keeps ADR 0027's original "everything tracked" behaviour and is
    the default so existing callers don't change.

    Non-git directories (no ``.git``) and files matching the skip lists
    are filtered out. Size-capped at 500 KB per file; binaries are
    detected by null-byte sniff on the first 8 KB. Errors on individual
    files never escape — that file is skipped and the walk continues.
    """
    blocks, _ = build_blocks_with_stats(repo_path, profile=profile)
    return blocks


def build_blocks_with_stats(
    repo_path: Path,
    *,
    profile: Profile | None = None,
) -> tuple[list[CodeBlock], RepoIndexResult]:
    """Same as :func:`build_blocks_for_repo` but also return profile stats.

    ``files_indexed`` counts files that produced at least one block;
    ``files_skipped`` counts files filtered out by the profile (does
    not include files dropped by the per-file skip rules — those are
    universal and not profile-specific).
    """
    profile_name = profile.name if profile is not None else "unknown"
    if not (repo_path / ".git").exists():
        return [], RepoIndexResult(profile_name, 0, 0)

    tracked = _git_ls_files(repo_path)
    blocks: list[CodeBlock] = []
    repo_str = str(repo_path)
    files_indexed = 0
    files_skipped_by_profile = 0

    for rel in tracked:
        if profile is not None and not profile.accepts(rel):
            files_skipped_by_profile += 1
            continue
        abs_path = repo_path / rel
        if _should_skip(rel, abs_path):
            continue
        try:
            file_blocks = _blocks_for_file(repo_str, abs_path)
        except (OSError, UnicodeDecodeError):
            continue
        if file_blocks:
            files_indexed += 1
            blocks.extend(file_blocks)

    return blocks, RepoIndexResult(
        profile_name=profile_name,
        files_indexed=files_indexed,
        files_skipped=files_skipped_by_profile,
    )


# ----- git listing ----------------------------------------------------------


def _git_ls_files(repo_path: Path) -> list[str]:
    """Return tracked files (git ls-files) or [] on any failure."""
    try:
        proc = subprocess.run(  # noqa: S603 — args list, no shell
            ["git", "-C", str(repo_path), "ls-files", "-z"],
            check=False,
            capture_output=True,
            timeout=_GIT_LS_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    try:
        text = proc.stdout.decode("utf-8", errors="replace")
    except (UnicodeDecodeError, AttributeError):
        return []
    return [piece for piece in text.split("\x00") if piece]


# ----- per-file extraction --------------------------------------------------


def _should_skip(rel: str, abs_path: Path) -> bool:
    """True if the file is ignored (vendor dir, lockfile, binary asset)."""
    parts = Path(rel).parts
    if any(segment in _SKIP_PATH_SEGMENTS for segment in parts):
        return True
    lower = rel.lower()
    if any(lower.endswith(suf) for suf in _SKIP_DOUBLE_SUFFIXES):
        return True
    suffix = abs_path.suffix.lower()
    if suffix in _SKIP_SUFFIXES:
        return True
    try:
        size = abs_path.stat().st_size
    except OSError:
        return True
    if size > _MAX_FILE_BYTES:
        return True
    return size == 0


def _blocks_for_file(repo_path: str, abs_path: Path) -> list[CodeBlock]:
    """Read a file, sniff for binary, slide windows, attach symbols."""
    # Binary sniff on raw bytes.
    with abs_path.open("rb") as fh:
        head = fh.read(_BINARY_SNIFF_BYTES)
    if b"\x00" in head:
        return []

    text = abs_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)
    if total == 0:
        return []

    updated_at = abs_path.stat().st_mtime
    language_ext = abs_path.suffix.lstrip(".").lower()

    blocks: list[CodeBlock] = []
    start_idx = 0
    while True:
        end_idx = min(start_idx + _WINDOW_SIZE, total)
        window_lines = lines[start_idx:end_idx]
        content = "\n".join(window_lines)
        symbol = _extract_symbol(window_lines)
        blocks.append(
            CodeBlock(
                repo_path=repo_path,
                path=str(abs_path),
                language_ext=language_ext,
                start_line=start_idx + 1,
                end_line=end_idx,
                content=content,
                symbol=symbol,
                updated_at=updated_at,
            )
        )
        if end_idx >= total:
            break
        start_idx += _WINDOW_STRIDE
        # Don't emit a window whose start is past the file end.
        if start_idx >= total:
            break

    return blocks


def _extract_symbol(window_lines: list[str]) -> str | None:
    """First identifier declared in the block's first non-blank line."""
    for line in window_lines:
        if not line.strip():
            continue
        match = _SYMBOL_RE.match(line)
        if match:
            return match.group(1)
        return None
    return None

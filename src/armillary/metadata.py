"""Per-project metadata extraction.

For git projects: branch, head commit (sha + timestamp + author),
dirty file count via GitPython. For all projects: README excerpt
(first 2-3 sentences) and ADR file list.

The extractor is **fault-tolerant by design**. A broken repo, missing
README, unreadable ADR directory, or unexpected GitPython exception
must never crash a scan — it just leaves the affected fields `None`.
The dashboard and `armillary list` then fall back to filesystem-only
signals for that project.

Parallelism: `extract_all()` uses a thread pool because GitPython
operations are I/O-bound (they shell out to `git`). For ~100 repos
this brings a sequential scan down from "annoying" to "instant" on
modern disks.

No status heuristics here — that lives in `status.py`. This module
just collects facts.
"""

from __future__ import annotations

import contextlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import git

from .models import Project, ProjectMetadata, ProjectType

DEFAULT_WORKERS = 4

# README candidates in priority order. The first existing file wins.
_README_CANDIDATES = (
    "README.md",
    "README.rst",
    "README.txt",
    "README",
    "readme.md",
)

# Where to look for Architecture Decision Records.
_ADR_DIRECTORIES = (
    "adr",
    "docs/adr",
    "decisions",
    "doc/adr",
)

# Where to look for free-form notes (not ADRs, not README). Markdown files
# in these directories get listed under `ProjectMetadata.note_paths` so the
# detail view can link them.
_NOTE_DIRECTORIES = (
    ".",  # root-level *.md (excluding README, which lives elsewhere)
    "notes",
    "docs",
)

_README_EXCERPT_MAX_CHARS = 280

# When walking the project tree for size/file_count, ignore noisy
# directories that bloat the numbers without telling us anything useful.
_SIZE_WALK_IGNORES = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        ".DS_Store",
    }
)


def extract(project: Project) -> ProjectMetadata:
    """Synchronously collect metadata for one project.

    All exceptions are swallowed and converted to "field stays None",
    so callers can rely on getting a `ProjectMetadata` instance back
    no matter how broken the underlying repo is.
    """
    md = ProjectMetadata()

    if project.type is ProjectType.GIT:
        # noqa: BLE001 — fault tolerance is the entire point of this layer.
        with contextlib.suppress(Exception):
            _fill_git_fields(project.path, md)

    with contextlib.suppress(Exception):
        md.readme_excerpt = _extract_readme_excerpt(project.path)

    try:
        md.adr_paths = _find_adr_files(project.path)
    except Exception:  # noqa: BLE001
        md.adr_paths = []

    try:
        md.note_paths = _find_note_files(project.path)
    except Exception:  # noqa: BLE001
        md.note_paths = []

    with contextlib.suppress(Exception):
        size_bytes, file_count = _compute_size_and_count(project.path)
        md.size_bytes = size_bytes
        md.file_count = file_count

    return md


def extract_all(
    projects: list[Project],
    *,
    workers: int = DEFAULT_WORKERS,
) -> None:
    """Extract metadata for every project in parallel and attach in place.

    Order of `projects` is preserved. Each project's `metadata` field is
    overwritten with the freshly-extracted value (or an empty
    `ProjectMetadata` if extraction returned None for some reason).
    """
    if not projects:
        return
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(extract, projects))
    for project, md in zip(projects, results, strict=True):
        project.metadata = md or ProjectMetadata()


# --- git fields ------------------------------------------------------------


def _fill_git_fields(repo_path: Path, md: ProjectMetadata) -> None:
    """Populate the git-specific fields on `md` from a repo at `repo_path`.

    Wrapped by `extract()` in a try/except, so individual exceptions
    here are fine — they just abort the rest of the git fill.
    """
    repo = git.Repo(repo_path)

    # Branch name (None when in detached HEAD state, e.g. mid-rebase).
    if not repo.head.is_detached:
        try:
            md.branch = repo.active_branch.name
        except (TypeError, ValueError):
            md.branch = None

    # HEAD commit — use rev-parse via repo.head.commit which is cheap.
    head = repo.head.commit
    md.last_commit_sha = head.hexsha
    md.last_commit_ts = datetime.fromtimestamp(head.committed_date)
    md.last_commit_author = head.author.name

    # Dirty count: anything that would show up under `git status`.
    # `index.diff(None)` covers unstaged working-tree edits, but misses
    # files that are staged-but-uncommitted — we need `index.diff("HEAD")`
    # for those, otherwise `git add some-file && armillary scan` would
    # incorrectly look like a clean repo.
    try:
        unstaged = len(repo.index.diff(None))
    except Exception:  # noqa: BLE001
        unstaged = 0
    try:
        staged = len(repo.index.diff("HEAD"))
    except Exception:  # noqa: BLE001
        staged = 0
    try:
        untracked = len(repo.untracked_files)
    except Exception:  # noqa: BLE001
        untracked = 0
    md.dirty_count = unstaged + staged + untracked

    # Ahead/behind vs upstream tracking branch. None of these fields
    # apply for repos with no upstream configured (the common case for
    # local-only branches), so an absent tracking branch leaves both
    # fields as None rather than 0.
    if md.branch is not None:
        with contextlib.suppress(Exception):
            tracking = repo.active_branch.tracking_branch()
            if tracking is not None:
                md.ahead = sum(1 for _ in repo.iter_commits(f"{tracking}..HEAD"))
                md.behind = sum(1 for _ in repo.iter_commits(f"HEAD..{tracking}"))


# --- README ---------------------------------------------------------------

_HEADER_RE = re.compile(r"^#{1,6}\s")
_INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")


def _extract_readme_excerpt(project_path: Path) -> str | None:
    """Find the first README in `project_path` and return its first
    paragraph or so as plain text. Returns None if no README exists.
    """
    for name in _README_CANDIDATES:
        readme = project_path / name
        if readme.is_file():
            try:
                content = readme.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            return _first_paragraph_plain(content)
    return None


def _first_paragraph_plain(markdown: str) -> str | None:
    """Strip headers, code blocks, and inline markdown from the first
    non-empty paragraph and clamp to ~280 characters.
    """
    in_code_fence = False
    paragraph: list[str] = []

    for raw_line in markdown.splitlines():
        line = raw_line.strip()

        if line.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue

        if not line:
            if paragraph:
                break
            continue

        if _HEADER_RE.match(line):
            continue

        # Strip simple inline markdown noise so the excerpt reads naturally.
        line = _INLINE_LINK_RE.sub(r"\1", line)
        line = _INLINE_CODE_RE.sub(r"\1", line)
        paragraph.append(line)

    if not paragraph:
        return None
    text = " ".join(paragraph).strip()
    if len(text) <= _README_EXCERPT_MAX_CHARS:
        return text
    cut = text[:_README_EXCERPT_MAX_CHARS]
    # Avoid cutting mid-word if there is a sensible space to break on.
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


# --- ADRs -----------------------------------------------------------------


def _find_adr_files(project_path: Path) -> list[Path]:
    """Look for ADRs in conventional locations and return their paths.

    Returns at most one match per directory; we glob *.md alphabetically
    so the dashboard can show them in a stable order.
    """
    found: list[Path] = []
    for rel in _ADR_DIRECTORIES:
        adr_dir = project_path / rel
        if not adr_dir.is_dir():
            continue
        try:
            adrs = sorted(adr_dir.glob("*.md"))
        except OSError:
            continue
        found.extend(adrs)
    return found


# --- notes ----------------------------------------------------------------

# README files are intentionally excluded from notes; they live in their
# own field. Lowercase comparison so README.md / readme.md / README.MD
# all collapse to the same exclusion.
_README_FILENAMES_LOWER = frozenset(name.lower() for name in _README_CANDIDATES)


def _find_note_files(project_path: Path) -> list[Path]:
    """List `.md` files in `./`, `notes/`, and `docs/` (excluding README).

    PLAN.md §5 M2 spec: "Notes detection: list `.md` files in root +
    `notes/` + `docs/`". Returns sorted, deduplicated paths so the
    dashboard can render them in a stable order. Files inside ADR
    directories are NOT excluded here — same `.md` may legitimately
    show up under both `adr_paths` and `note_paths`.
    """
    found: set[Path] = set()
    for rel in _NOTE_DIRECTORIES:
        note_dir = project_path / rel if rel != "." else project_path
        if not note_dir.is_dir():
            continue
        try:
            for entry in note_dir.iterdir():
                if not entry.is_file():
                    continue
                if entry.suffix.lower() != ".md":
                    continue
                if entry.name.lower() in _README_FILENAMES_LOWER:
                    continue
                found.add(entry)
        except OSError:
            continue
    return sorted(found)


# --- size and file count --------------------------------------------------


def _compute_size_and_count(project_path: Path) -> tuple[int, int]:
    """Walk the project tree and return (total_bytes, file_count).

    Skips well-known noise directories (`.git`, `node_modules`, `.venv`,
    build artifacts, ...) so the numbers reflect "what the user wrote",
    not "what the package manager downloaded". Symlinks are followed
    only via `Path.stat()` (no recursion into symlinked directories,
    matching the scanner's policy).
    """
    total_bytes = 0
    file_count = 0
    stack: list[Path] = [project_path]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.name in _SIZE_WALK_IGNORES:
                continue
            try:
                if entry.is_symlink():
                    # Don't follow directory symlinks (could cycle).
                    continue
                if entry.is_dir():
                    stack.append(entry)
                elif entry.is_file():
                    file_count += 1
                    total_bytes += entry.stat().st_size
            except (PermissionError, OSError):
                continue
    return total_bytes, file_count

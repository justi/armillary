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

_README_EXCERPT_MAX_CHARS = 280


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

    # Dirty count: changes-vs-index plus untracked files. Cheap because
    # it shells out to `git status` once.
    try:
        modified = len(repo.index.diff(None))
    except Exception:  # noqa: BLE001
        modified = 0
    try:
        untracked = len(repo.untracked_files)
    except Exception:  # noqa: BLE001
        untracked = 0
    md.dirty_count = modified + untracked


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

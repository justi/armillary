"""Project auto-discovery — walk umbrella folders, return `Project` list.

Rules (per PLAN.md §5 M1 Phase 3):

1.  A directory containing a `.git` child  → `ProjectType.GIT`;
    stop descending (a repo is one project, not many).
2.  A directory containing at least one `.md` or `.ipynb` file directly
    and no `.git`  → `ProjectType.IDEA`; stop descending.
3.  A directory whose only "doc-bearing" subfolder is a single `docs/` /
    `notes/` / `research/`-style folder containing `.md` / `.ipynb`
    (and which has no git subfolder)  → `ProjectType.IDEA`; stop
    descending. This catches `myproject/docs/README.md` → `myproject`
    instead of marking `docs` as the project. A folder with multiple
    doc-bearing siblings still recurses, so `research/{p1,p2}` produces
    `p1` and `p2` as separate projects.
4.  Otherwise recurse into subdirectories until `umbrella.max_depth`.

The umbrella root itself is never emitted as a project (we start at
depth 1, not 0). Hidden dirs and entries in `DEFAULT_IGNORES` are skipped.
Symlinked directories are **not** followed — this avoids cycles and
prevents the same project from appearing twice via aliases.

Suffix matching is case-insensitive (so `README.MD` and `Notebook.IPYNB`
on macOS APFS are detected the same as the lowercase forms).

No git reads, no README parsing, no status heuristics here — that lives
in `metadata.py` / `status.py` (M3).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .models import Project, ProjectType, UmbrellaFolder

DEFAULT_IGNORES: frozenset[str] = frozenset(
    {
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
        ".Trash",
        ".DS_Store",
        "_archive",
    }
)

IDEA_FILE_SUFFIXES: frozenset[str] = frozenset({".md", ".ipynb"})


def scan(
    umbrellas: Iterable[UmbrellaFolder],
    *,
    ignores: frozenset[str] = DEFAULT_IGNORES,
) -> list[Project]:
    """Scan multiple umbrella folders and return a merged, deduped project list.

    Projects are keyed by `Project.path`, which `_make_project` canonicalizes
    via `Path.resolve()`. Overlapping umbrellas (e.g. ``-u ~/Projects -u
    ~/Projects/work``), repeated `-u` flags, and umbrella arguments containing
    `..` therefore all collapse to a single entry per project. The first
    umbrella to discover a given project wins — its `umbrella` field is
    preserved.
    """
    seen: dict[Path, Project] = {}
    for umbrella in umbrellas:
        for project in scan_umbrella(umbrella, ignores=ignores):
            seen.setdefault(project.path, project)
    return list(seen.values())


def scan_umbrella(
    umbrella: UmbrellaFolder,
    *,
    ignores: frozenset[str] = DEFAULT_IGNORES,
) -> list[Project]:
    """Walk one umbrella folder and return all projects found inside."""
    root = umbrella.path.expanduser().resolve()
    if not root.is_dir():
        return []

    projects: list[Project] = []
    _walk(
        current=root,
        umbrella_root=root,
        max_depth=umbrella.max_depth,
        depth=0,
        out=projects,
        ignores=ignores,
    )
    return projects


def _walk(
    *,
    current: Path,
    umbrella_root: Path,
    max_depth: int,
    depth: int,
    out: list[Project],
    ignores: frozenset[str],
) -> None:
    """Recursively walk `current`, appending detected projects to `out`."""
    if depth > max_depth:
        return

    try:
        entries = list(current.iterdir())
    except (PermissionError, OSError):
        return

    # Never classify the umbrella root itself as a project.
    if depth > 0:
        if _is_git_project(current):
            out.append(_make_project(current, umbrella_root, ProjectType.GIT))
            return
        if _has_direct_idea_files(entries):
            out.append(_make_project(current, umbrella_root, ProjectType.IDEA))
            return
        if _is_single_doc_folder_parent(entries, ignores):
            out.append(_make_project(current, umbrella_root, ProjectType.IDEA))
            return

    # Otherwise descend into subdirectories.
    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.is_symlink():
            # Don't follow directory symlinks: avoids cycles and prevents
            # the same project being indexed twice via an alias.
            continue
        if _should_skip(entry, ignores):
            continue
        _walk(
            current=entry,
            umbrella_root=umbrella_root,
            max_depth=max_depth,
            depth=depth + 1,
            out=out,
            ignores=ignores,
        )


def _is_git_project(path: Path) -> bool:
    return (path / ".git").exists()


def _has_direct_idea_files(entries: list[Path]) -> bool:
    """True if any direct child is a `.md` / `.ipynb` file (case-insensitive)."""
    return any(e.is_file() and e.suffix.lower() in IDEA_FILE_SUFFIXES for e in entries)


def _is_single_doc_folder_parent(entries: list[Path], ignores: frozenset[str]) -> bool:
    """True if the current folder looks like a project whose notes/docs live
    one level deep — exactly one direct subfolder contains `.md` / `.ipynb`
    files directly, and no direct subfolder is a git repo.

    This catches the common layouts:

        myproject/docs/README.md      → myproject is idea
        myproject/notes/2024-01.md    → myproject is idea
        research/notebook.ipynb       → research is idea (if research has
                                        no other doc-bearing siblings)

    A folder with **multiple** doc-bearing direct subfolders is treated as
    a container of projects and recurses normally, so:

        research-area/project1/README.md
        research-area/project2/notes.md

    yields `project1` and `project2` separately, not `research-area`.
    """
    doc_bearing = 0
    for entry in entries:
        if not entry.is_dir() or entry.is_symlink():
            continue
        if _should_skip(entry, ignores):
            continue
        # A git subfolder means this isn't a "doc folder parent" — it's a
        # container of real projects. Let the recursion handle it.
        if (entry / ".git").exists():
            return False
        try:
            children = list(entry.iterdir())
        except (PermissionError, OSError):
            continue
        if any(
            c.is_file() and c.suffix.lower() in IDEA_FILE_SUFFIXES for c in children
        ):
            doc_bearing += 1
            if doc_bearing > 1:
                return False
    return doc_bearing == 1


def _should_skip(path: Path, ignores: frozenset[str]) -> bool:
    name = path.name
    return name in ignores or name.startswith(".") or name.endswith(".egg-info")


def _make_project(path: Path, umbrella_root: Path, type_: ProjectType) -> Project:
    # Canonicalize before storing so that downstream dedup keys, cache rows,
    # and launcher invocations all see the same normalized path. With the
    # symlink skip in `_walk` this is mostly belt-and-suspenders, but it also
    # collapses any `..` segments introduced by an oddly-shaped umbrella arg.
    resolved = path.resolve()
    return Project(
        path=resolved,
        name=resolved.name,
        type=type_,
        umbrella=umbrella_root,
        last_modified=_compute_last_modified(resolved),
    )


def _compute_last_modified(path: Path) -> datetime:
    """Best-effort 'last touched' timestamp without recursing.

    `path.stat().st_mtime` on a directory only changes when entries are
    added or removed at that level — editing `README.md` in place leaves
    the parent dir mtime untouched. We therefore take the max over the
    directory itself **and** its immediate children (root-level edits
    to files like README, pyproject.toml, package.json, ...).

    `.git/` is **deliberately excluded** from the candidates. GitPython
    operations during metadata extraction (`repo.untracked_files` shells
    out to `git status`, which refreshes `.git/index`) bump the `.git/`
    mtime as a side effect — using it as a "last edit" signal would make
    every freshly-scanned project look like it was just touched. The
    canonical "when was this repo last edited" answer for git projects
    is `metadata.last_commit_ts`, which the CLI overrides on top of this
    field after metadata extraction.

    For non-git "idea" folders this is still the only signal we have, so
    edits to root-level notes still get picked up correctly.
    """
    candidates: list[float] = []
    with contextlib.suppress(PermissionError, OSError):
        candidates.append(path.stat().st_mtime)

    with contextlib.suppress(PermissionError, OSError):
        for entry in path.iterdir():
            if entry.name == ".git":
                continue
            with contextlib.suppress(PermissionError, OSError):
                candidates.append(entry.stat().st_mtime)

    if not candidates:
        # Path was unreadable; degrade gracefully to epoch.
        return datetime.fromtimestamp(0)
    return datetime.fromtimestamp(max(candidates))

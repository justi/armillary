"""Project auto-discovery — walk umbrella folders, return `Project` list.

Rules (per PLAN.md §5 M1 Phase 3):

1.  A directory containing a `.git` child  → `ProjectType.GIT`;
    stop descending (a repo is one project, not many).
2.  A directory containing at least one `.md` or `.ipynb` file directly
    and no `.git`  → `ProjectType.IDEA`; stop descending.
3.  Otherwise recurse into subdirectories until `umbrella.max_depth`.

The umbrella root itself is never emitted as a project (we start at
depth 1, not 0). Hidden dirs and entries in `DEFAULT_IGNORES` are skipped.

No git reads, no README parsing, no status heuristics here — that lives
in `metadata.py` / `status.py` (M3).
"""

from __future__ import annotations

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

    Projects are keyed by their resolved `Path`, so overlapping umbrellas
    (e.g. ``-u ~/Projects -u ~/Projects/work``) or repeated `-u` flags do
    not produce duplicate entries. The first umbrella to discover a given
    project wins — its `umbrella` field is preserved.
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
        if _is_idea_project(entries):
            out.append(_make_project(current, umbrella_root, ProjectType.IDEA))
            return

    # Otherwise descend into subdirectories.
    for entry in entries:
        if not entry.is_dir():
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


def _is_idea_project(entries: list[Path]) -> bool:
    return any(e.is_file() and e.suffix in IDEA_FILE_SUFFIXES for e in entries)


def _should_skip(path: Path, ignores: frozenset[str]) -> bool:
    name = path.name
    if name in ignores:
        return True
    if name.startswith("."):
        return True
    if name.endswith(".egg-info"):
        return True
    return False


def _make_project(path: Path, umbrella_root: Path, type_: ProjectType) -> Project:
    return Project(
        path=path,
        name=path.name,
        type=type_,
        umbrella=umbrella_root,
        last_modified=_compute_last_modified(path),
    )


def _compute_last_modified(path: Path) -> datetime:
    """Best-effort 'last touched' timestamp without recursing.

    `path.stat().st_mtime` on a directory only changes when entries are
    added or removed at that level — editing `README.md` in place leaves
    the parent dir mtime untouched. We therefore take the max over the
    directory itself **and** its immediate children, which catches:

    - root-level edits (README, pyproject.toml, package.json, ...);
    - git activity via `.git/` (commits/checkouts mutate index, HEAD, refs).

    Edits deeper in the tree are still missed — that is M3's job, where
    `metadata.py` will use GitPython for git repos and a proper recursive
    walk for idea folders. For M2 this gives a clearly better signal than
    the bare directory mtime without paying for a full tree walk.
    """
    candidates: list[float] = []
    try:
        candidates.append(path.stat().st_mtime)
    except (PermissionError, OSError):
        pass

    try:
        for entry in path.iterdir():
            try:
                candidates.append(entry.stat().st_mtime)
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass

    if not candidates:
        # Path was unreadable; degrade gracefully to epoch.
        return datetime.fromtimestamp(0)
    return datetime.fromtimestamp(max(candidates))

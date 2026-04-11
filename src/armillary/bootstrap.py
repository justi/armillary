"""First-run umbrella folder discovery for `armillary config --init`.

PLAN.md §5 "Bootstrap" describes a two-phase first-run experience:

1. Shallow scan of `~/` (maxdepth=2) looking for umbrella candidates
2. Interactive picker → save selection to `~/.config/armillary/config.yaml`

This module owns phase 1: the heuristic search. Phase 2 lives in
`cli.config` because it needs typer for the interactive prompts.

A folder is an umbrella candidate if it satisfies **either** of:

- it contains 2+ direct children that look like git repos
  (each has a `.git` entry — not the umbrella itself), OR
- its name matches a conventional umbrella name (`Projects`, `repos`,
  `code`, ...)

System directories (`Library`, `Applications`, `node_modules`, ...) are
excluded so the candidate list stays short and meaningful.

Returned candidates carry enough metadata for the picker to display a
useful prompt: number of git repos, number of idea folders, last
modification time of the umbrella itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Conventional umbrella folder names. Lowercased for case-insensitive
# matching against directory names.
_CONVENTIONAL_UMBRELLA_NAMES: frozenset[str] = frozenset(
    {
        "projects",
        "projects_prod",
        "projects-prod",
        "repos",
        "repositories",
        "code",
        "work",
        "src",
        "dev",
        "development",
        "workspace",
        "workspaces",
        "git",
    }
)

# Top-level directories under `~/` that we never want to recurse into.
# These either contain system data, are package-manager caches, or are
# typical macOS / Linux user folders that hold non-project content.
_SYSTEM_FOLDERS: frozenset[str] = frozenset(
    {
        "Library",
        "Applications",
        ".Trash",
        ".cache",
        ".npm",
        ".cargo",
        ".rustup",
        ".gem",
        ".local",
        ".nvm",
        "Music",
        "Movies",
        "Pictures",
        "Public",
        "Downloads",
        "Desktop",
    }
)

# Folders we never want to count as repos / recurse into when scanning
# directly inside an umbrella candidate.
_NOISE_FOLDERS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".tox",
        "dist",
        "build",
        ".cache",
    }
)


@dataclass(frozen=True)
class UmbrellaCandidate:
    """One folder that looks like it could hold projects.

    `score` is a rough sort key (more git repos = higher), used by the
    picker to put the most promising candidates at the top of the list.
    """

    path: Path
    git_count: int
    idea_count: int
    last_modified: datetime
    name_match: bool

    @property
    def total_projects(self) -> int:
        return self.git_count + self.idea_count

    @property
    def score(self) -> tuple[int, int, float]:
        """Sort tuple — descending priority by git_count, then total, then mtime."""
        return (
            self.git_count,
            self.total_projects,
            self.last_modified.timestamp(),
        )


def discover_umbrella_candidates(
    home: Path | None = None,
    *,
    min_git_repos: int = 2,
) -> list[UmbrellaCandidate]:
    """Find umbrella folder candidates under `home` (default `~`).

    Returns candidates sorted by descending score (most-promising first).
    The scan goes only one level deep into `home` and one level deeper
    into each candidate, so it stays fast on large home directories.

    `min_git_repos` is the threshold for the "has multiple git repos"
    rule. Conventional-name folders are always returned, even if they
    contain fewer repos.
    """
    home = (home or Path.home()).expanduser()
    if not home.is_dir():
        return []

    candidates: list[UmbrellaCandidate] = []
    try:
        top_level = list(home.iterdir())
    except (PermissionError, OSError):
        return []

    for entry in top_level:
        if not _should_inspect(entry):
            continue
        try:
            git_count, idea_count = _count_children(entry)
        except (PermissionError, OSError):
            continue

        name_match = entry.name.lower() in _CONVENTIONAL_UMBRELLA_NAMES
        meets_threshold = git_count >= min_git_repos
        if not (meets_threshold or name_match):
            continue
        # Skip empty conventional folders — they don't help anyone.
        if name_match and git_count == 0 and idea_count == 0:
            continue

        try:
            mtime = datetime.fromtimestamp(entry.stat().st_mtime)
        except (PermissionError, OSError):
            mtime = datetime.fromtimestamp(0)

        candidates.append(
            UmbrellaCandidate(
                path=entry.resolve(),
                git_count=git_count,
                idea_count=idea_count,
                last_modified=mtime,
                name_match=name_match,
            )
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def _should_inspect(path: Path) -> bool:
    """True if `path` is a directory we are willing to peek into."""
    if not path.is_dir():
        return False
    if path.is_symlink():
        return False
    name = path.name
    if name.startswith("."):
        # Hidden folders (`.config`, `.ssh`, ...) — never umbrellas.
        return False
    return name not in _SYSTEM_FOLDERS


def _count_children(folder: Path) -> tuple[int, int]:
    """Return (git_count, idea_count) for direct children of `folder`.

    A child is "git" if it contains a `.git` entry (file or directory).
    A child is "idea" if it has at least one `.md` / `.ipynb` file
    directly inside it. The two are exclusive — git wins.
    """
    git_count = 0
    idea_count = 0
    try:
        children = list(folder.iterdir())
    except (PermissionError, OSError):
        return 0, 0

    for child in children:
        try:
            if not child.is_dir() or child.is_symlink():
                continue
        except (PermissionError, OSError):
            continue
        if child.name.startswith(".") or child.name in _NOISE_FOLDERS:
            continue

        try:
            has_git = (child / ".git").exists()
        except (PermissionError, OSError):
            continue
        if has_git:
            git_count += 1
            continue

        try:
            inner = list(child.iterdir())
        except (PermissionError, OSError):
            continue
        if any(e.is_file() and e.suffix.lower() in {".md", ".ipynb"} for e in inner):
            idea_count += 1

    return git_count, idea_count

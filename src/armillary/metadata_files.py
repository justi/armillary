"""Filesystem-derived metadata: ADR file list, free-form notes, and
size/file-count walk. Split out of ``metadata.py`` for the 400-line
architecture target (ADR 0001 rule 3).
"""

from __future__ import annotations

from pathlib import Path

from .metadata_readme import README_CANDIDATES

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

# README files are intentionally excluded from notes; they live in their
# own field. Lowercase comparison so README.md / readme.md / README.MD
# all collapse to the same exclusion.
_README_FILENAMES_LOWER = frozenset(name.lower() for name in README_CANDIDATES)

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


def _find_note_files(project_path: Path) -> list[Path]:
    """List `.md` files in `./`, `notes/`, and `docs/`.

    Notes detection: list `.md` files in root + `notes/` + `docs/`.
    Returns sorted, deduplicated paths so the
    dashboard can render them in a stable order.

    The **project-root** README is excluded because it already has its
    own dedicated `readme_excerpt` field. README files in subdirectories
    (`docs/README.md`, `notes/README.md`) are NOT excluded — they are
    legitimate notes / documentation indexes that nothing else surfaces.
    """
    found: set[Path] = set()
    for rel in _NOTE_DIRECTORIES:
        is_project_root = rel == "."
        note_dir = project_path if is_project_root else project_path / rel
        if not note_dir.is_dir():
            continue
        try:
            for entry in note_dir.iterdir():
                if not entry.is_file():
                    continue
                if entry.suffix.lower() != ".md":
                    continue
                # Only the project-root README is covered elsewhere.
                if is_project_root and entry.name.lower() in _README_FILENAMES_LOWER:
                    continue
                found.add(entry)
        except OSError:
            continue
    return sorted(found)


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

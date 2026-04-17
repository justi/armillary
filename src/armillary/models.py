"""Pydantic models for armillary.

These are the core domain types passed between the scanner, metadata
extractor, cache, and UI layers. The scanner fills in the cheap fields
on `Project`; M3.2's `metadata.extract()` populates `ProjectMetadata`
on top.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ProjectType(StrEnum):
    """What kind of thing a project directory is."""

    GIT = "git"
    """A folder containing a `.git` subdirectory."""

    IDEA = "idea"
    """A loose folder with notes/notebooks but no git history."""


class Status(StrEnum):
    """Where a project stands right now, derived from metadata + filesystem.

    The labels come from the project status heuristics. They are computed
    by `armillary.status.compute_status()` from `ProjectMetadata` plus
    the project's last filesystem modification time.
    """

    ACTIVE = "ACTIVE"
    """Recent commit or file edit (default cutoff: 7 days)."""

    PAUSED = "PAUSED"
    """Dirty working tree but no recent commits."""

    DORMANT = "DORMANT"
    """No changes for a while (default cutoff: 30 days)."""

    IDEA = "IDEA"
    """Loose notes folder, never been productized."""

    IN_PROGRESS = "IN_PROGRESS"
    """Idea folder with an open `[ ]` checkbox in TODO.md."""


class UmbrellaFolder(BaseModel):
    """A top-level folder under which armillary looks for projects.

    Umbrella folders come from the interactive bootstrap (M1 feature) or
    from `~/.config/armillary/config.yaml` (M5). The scanner walks each
    umbrella down to `max_depth` and emits one `Project` per hit.
    """

    model_config = ConfigDict(frozen=True)

    path: Path
    label: str | None = None
    max_depth: int = Field(default=3, ge=1, le=10)


class ProjectMetadata(BaseModel):
    """Rich per-project metadata.

    Populated by `armillary.metadata.extract()` (M3.2). All fields are
    optional — git fields are `None` for idea projects, README/ADR
    fields are `None` when the relevant files do not exist, and
    *every* field falls back to `None` if extraction fails entirely
    (broken repo, permission error, GitPython exception). The scanner
    must therefore never assume any of these are populated.
    """

    model_config = ConfigDict(extra="forbid")

    # Git fields — None for idea projects and broken repos.
    branch: str | None = None
    last_commit_sha: str | None = None
    last_commit_ts: datetime | None = None
    last_commit_author: str | None = None
    dirty_count: int | None = None
    ahead: int | None = None
    behind: int | None = None
    commit_count: int | None = None
    work_hours: float | None = None

    # Universal fields.
    size_bytes: int | None = None
    file_count: int | None = None
    readme_excerpt: str | None = None
    adr_paths: list[Path] = Field(default_factory=list)
    note_paths: list[Path] = Field(default_factory=list)

    # Decision signals (ADR 0017) — cached during scan.
    # S1: 4-week commit velocity [week4, week3, week2, week1].
    commit_velocity: list[int] | None = None
    # S1: trend derived from commit_velocity.
    velocity_trend: str | None = None  # rising / falling / flat / dead
    # S5: timestamp of the very first commit in the repo.
    first_commit_ts: datetime | None = None
    # Monthly activity: commit counts per month, last 6 months [oldest..newest].
    monthly_commits: list[int] | None = None
    # S6: total number of local branches.
    branch_count: int | None = None
    # S6: whether the repo has at least one remote configured.
    has_remote: bool | None = None

    # Computed by `status.compute_status()` after extract; lives here so
    # the cache and dashboard can both read it as part of `ProjectMetadata`.
    status: Status | None = None


class Project(BaseModel):
    """A single discovered project."""

    path: Path
    name: str
    type: ProjectType
    umbrella: Path
    last_modified: datetime
    metadata: ProjectMetadata | None = None

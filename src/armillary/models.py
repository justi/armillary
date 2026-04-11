"""Pydantic models for armillary.

These are the core domain types passed between the scanner, metadata
extractor, cache, and UI layers. Keep them minimal — richer fields
(git info, README preview, ADRs, status) are added in M3.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ProjectType(str, Enum):
    """What kind of thing a project directory is."""

    GIT = "git"
    """A folder containing a `.git` subdirectory."""

    IDEA = "idea"
    """A loose folder with notes/notebooks but no git history."""


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

    M2 leaves this empty on purpose — the scanner only fills in the
    cheap fields on `Project` itself. M3 populates this with GitPython
    output, README preview, ADR list, etc.
    """

    model_config = ConfigDict(extra="forbid")


class Project(BaseModel):
    """A single discovered project."""

    path: Path
    name: str
    type: ProjectType
    umbrella: Path
    last_modified: datetime
    metadata: ProjectMetadata | None = None

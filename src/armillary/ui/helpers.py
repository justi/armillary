"""Shared helpers, constants, and cached data loaders for the dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import streamlit as st

from armillary import scan_service
from armillary.cache import Cache
from armillary.config import Config
from armillary.models import Project, UmbrellaFolder
from armillary.utils import safe_load_config as _safe_load_config_impl
from armillary.utils import shorten_home as _shorten_home_impl

_STATUS_EMOJI = {
    "ACTIVE": "🟢",
    "PAUSED": "🟡",
    "DORMANT": "⚫",
    "IDEA": "💭",
    "IN_PROGRESS": "📝",
}


# --- typed view model ------------------------------------------------------


@dataclass(frozen=True)
class OverviewRow:
    """Typed view model for one row in the overview table.

    Replaces the old ``dict[str, Any]`` so every consumer has
    auto-complete, type checking, and a stable contract instead of
    stringly-typed key lookups. Frozen + hashable so Streamlit's
    ``st.cache_data`` can serialise it without surprises.
    """

    status_label: str  # e.g. "🟢 ACTIVE" or "—"
    status_raw: str  # e.g. "ACTIVE" or "" (for filters)
    type: str
    name: str
    branch: str
    dirty: int | None
    commits: int | None
    work_hours: float | None
    umbrella: str
    last_modified: datetime
    path: str  # canonical path string


# --- data loading ----------------------------------------------------------


@st.cache_data(ttl=60, show_spinner=False)
def _load_overview_rows() -> list[OverviewRow]:
    """Read the cache once per minute and return typed rows.

    ``OverviewRow`` is a frozen dataclass — hashable and pickle-friendly
    so Streamlit's data cache works without surprises.
    """
    with Cache() as cache:
        projects = cache.list_projects()
    return [_project_to_row(p) for p in projects]


def _project_to_row(p: Project) -> OverviewRow:
    md = p.metadata
    status_value = md.status.value if md and md.status else None
    status_label = (
        f"{_STATUS_EMOJI.get(status_value, '·')} {status_value}"
        if status_value
        else "—"
    )
    dirty_value = md.dirty_count if md and md.dirty_count is not None else None
    commit_count = md.commit_count if md and md.commit_count is not None else None
    work_hours = md.work_hours if md and md.work_hours is not None else None
    return OverviewRow(
        status_label=status_label,
        status_raw=status_value or "",
        type=p.type.value,
        name=p.name,
        branch=(md.branch if md else None) or "—",
        dirty=dirty_value,
        commits=commit_count,
        work_hours=work_hours,
        umbrella=_shorten_home(p.umbrella),
        last_modified=p.last_modified,
        path=str(p.path),
    )


def _shorten_home(path: Path) -> str:
    return _shorten_home_impl(path)


# --- shared scan operation -------------------------------------------------


def _run_dashboard_scan(cfg: Config | None) -> tuple[bool, str]:
    """Walk the configured umbrellas, persist to cache, return status.

    Delegates to :func:`scan_service.full_scan` which handles the full
    pipeline: scanner -> metadata -> status compute -> cache.upsert +
    prune. Then clears Streamlit's data caches so the rerender shows
    the fresh data.

    Returns `(ok, message)`. The caller is responsible for `st.rerun()`.

    This is the **only** place in the dashboard where filesystem walks
    are allowed. The hot path stays read-only — this function only runs
    when the user explicitly clicks "Scan filesystem now".
    """
    if cfg is None:
        return False, "Config could not be loaded. Check your config.yaml syntax."
    if not cfg.umbrellas:
        return (
            False,
            "No umbrellas configured. Run `armillary config --init` from "
            "your terminal to set up.",
        )

    try:
        umbrellas = [
            UmbrellaFolder(path=u.path, label=u.label, max_depth=u.max_depth)
            for u in cfg.umbrellas
        ]
        projects = scan_service.full_scan(umbrellas, write_metadata=True)
    except Exception as exc:  # noqa: BLE001 — surface error to the UI, not crash
        return False, f"Scan failed: {exc}"

    # Both data caches must be cleared BEFORE the rerun so the next
    # rerender reads fresh rows instead of the 60-second-stale TTL.
    _load_overview_rows.clear()
    _load_project.clear()

    return True, f"Scanned {len(projects)} project(s)."


def _safe_load_config() -> Config | None:
    """Load the config file, swallowing errors so the dashboard can still
    render the table even if config is broken (CLI features that need
    config — launcher dropdown — degrade gracefully)."""
    return _safe_load_config_impl()


@st.cache_data(ttl=60, show_spinner=False)
def _load_project(project_path: str) -> Project | None:
    """Look up one project by its canonical path. Cached separately from
    the overview rows so navigating into a detail page does not invalidate
    the table list."""
    with Cache() as cache:
        return cache.get_project(project_path)

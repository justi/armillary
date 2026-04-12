"""Shared helpers, constants, and cached data loaders for the dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from armillary import scan_service
from armillary.cache import Cache
from armillary.config import Config, ConfigError, load_config
from armillary.models import Project, UmbrellaFolder

_STATUS_EMOJI = {
    "ACTIVE": "🟢",
    "PAUSED": "🟡",
    "DORMANT": "⚫",
    "IDEA": "💭",
    "IN_PROGRESS": "📝",
}


# --- data loading ----------------------------------------------------------


@st.cache_data(ttl=60, show_spinner=False)
def _load_overview_rows() -> list[dict[str, Any]]:
    """Read the cache once per minute and return rows as plain dicts.

    Returning dicts (rather than `Project` instances) keeps the cached
    payload trivially hashable and pickle-friendly so Streamlit's data
    cache works without surprises.
    """
    with Cache() as cache:
        projects = cache.list_projects()
    return [_project_to_row(p) for p in projects]


def _project_to_row(p: Project) -> dict[str, Any]:
    md = p.metadata
    status_value = md.status.value if md and md.status else None
    status_label = (
        f"{_STATUS_EMOJI.get(status_value, '·')} {status_value}"
        if status_value
        else "—"
    )
    # `dirty_count` stays None when metadata was never extracted (e.g.
    # `armillary scan --no-metadata` or a repo whose extraction failed).
    # Coercing it to 0 would lie — the dashboard would claim the working
    # tree is clean even though it never looked. Let Streamlit's
    # NumberColumn render the absence as an empty cell instead.
    dirty_value = md.dirty_count if md and md.dirty_count is not None else None
    commit_count = md.commit_count if md and md.commit_count is not None else None
    work_hours = md.work_hours if md and md.work_hours is not None else None
    return {
        "Status": status_label,
        "Type": p.type.value,
        "Name": p.name,
        "Branch": (md.branch if md else None) or "—",
        "Dirty": dirty_value,
        "Commits": commit_count,
        "Work h": work_hours,
        "Umbrella": _shorten_home(p.umbrella),
        "Last modified": p.last_modified,
        # Hidden columns used by the row-click handler.
        "_path": str(p.path),
        "_status_raw": status_value or "",
    }


def _shorten_home(path: Path) -> str:
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home) :] if s.startswith(home) else s


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
    try:
        return load_config()
    except ConfigError:
        return None


@st.cache_data(ttl=60, show_spinner=False)
def _load_project(project_path: str) -> Project | None:
    """Look up one project by its canonical path. Cached separately from
    the overview rows so navigating into a detail page does not invalidate
    the table list."""
    with Cache() as cache:
        return cache.get_project(project_path)

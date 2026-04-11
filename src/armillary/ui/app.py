"""Streamlit dashboard for armillary.

The dashboard is a **read-only consumer of the SQLite cache**. It never
runs the scanner, never opens GitPython, never reads README files —
that all happens in `armillary scan` from the CLI. Streamlit reruns
the whole script on every interaction, so anything expensive in this
file would tank the dashboard the moment a user with 100+ projects
clicked a filter.

The two views (overview table and per-project detail) live in the same
script and route via `st.query_params["project"]`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# When Streamlit launches this script directly via `streamlit run` it
# does not always see the editable install on `sys.path`, so we add the
# src directory ourselves. The CLI's `armillary start` command always
# invokes `python -m streamlit run <this file>` with the venv's Python,
# so this just makes things robust against other invocation styles.
_PKG_ROOT = Path(__file__).resolve().parents[2]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

import streamlit as st  # noqa: E402

from armillary import __version__  # noqa: E402
from armillary.cache import Cache  # noqa: E402
from armillary.models import Project  # noqa: E402

st.set_page_config(
    page_title="armillary",
    page_icon="🔭",
    layout="wide",
)


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
    return {
        "Status": status_label,
        "Type": p.type.value,
        "Name": p.name,
        "Branch": (md.branch if md else None) or "—",
        "Dirty": (md.dirty_count if md and md.dirty_count is not None else 0),
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


# --- routing ---------------------------------------------------------------


def main() -> None:
    params = st.query_params
    project_path = params.get("project")
    if project_path:
        _render_project_detail(project_path)
    else:
        _render_overview()


# --- overview --------------------------------------------------------------


def _render_overview() -> None:
    st.title("🔭 armillary")
    st.caption(f"v{__version__} · project observatory")

    rows = _load_overview_rows()
    if not rows:
        st.info(
            "No projects in cache yet. Run `armillary scan -u <path>` from "
            "your terminal to populate the dashboard."
        )
        return

    filters = _render_sidebar(rows)
    search = st.text_input(
        "🔍 Search by name",
        placeholder="Type to filter…",
        label_visibility="collapsed",
    )

    filtered = _apply_filters(rows, filters=filters, search=search)
    _render_summary_metrics(filtered)
    st.divider()

    if not filtered:
        st.warning("No projects match the current filters.")
        return

    _render_table(filtered)


def _render_sidebar(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    with st.sidebar:
        st.header("Filters")

        statuses = sorted({r["_status_raw"] for r in rows if r["_status_raw"]})
        types = sorted({r["Type"] for r in rows})
        umbrellas = sorted({r["Umbrella"] for r in rows})

        status_pick = st.multiselect("Status", statuses)
        type_pick = st.multiselect("Type", types)
        umbrella_pick = st.multiselect("Umbrella", umbrellas)

        st.divider()
        st.caption(f"{len(rows)} projects in cache")
        if st.button("🔄 Refresh from cache", use_container_width=True):
            _load_overview_rows.clear()
            st.rerun()

    return {
        "status": status_pick,
        "type": type_pick,
        "umbrella": umbrella_pick,
    }


def _apply_filters(
    rows: list[dict[str, Any]],
    *,
    filters: dict[str, list[str]],
    search: str,
) -> list[dict[str, Any]]:
    out = rows
    if filters["status"]:
        out = [r for r in out if r["_status_raw"] in filters["status"]]
    if filters["type"]:
        out = [r for r in out if r["Type"] in filters["type"]]
    if filters["umbrella"]:
        out = [r for r in out if r["Umbrella"] in filters["umbrella"]]
    if search:
        needle = search.lower()
        out = [r for r in out if needle in r["Name"].lower()]
    return out


def _render_summary_metrics(rows: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["_status_raw"]] = counts.get(r["_status_raw"], 0) + 1
    cols = st.columns(5)
    cols[0].metric("Total", len(rows))
    cols[1].metric("Active", counts.get("ACTIVE", 0))
    cols[2].metric("Paused", counts.get("PAUSED", 0))
    cols[3].metric("Dormant", counts.get("DORMANT", 0))
    cols[4].metric(
        "Ideas",
        counts.get("IDEA", 0) + counts.get("IN_PROGRESS", 0),
    )


def _render_table(rows: list[dict[str, Any]]) -> None:
    display = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    event = st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Last modified": st.column_config.DatetimeColumn(
                "Last modified",
                format="YYYY-MM-DD HH:mm",
            ),
            "Dirty": st.column_config.NumberColumn("Dirty", format="%d"),
        },
    )

    selection = getattr(event, "selection", None)
    selected_rows = getattr(selection, "rows", []) if selection else []
    if selected_rows:
        idx = selected_rows[0]
        st.query_params["project"] = rows[idx]["_path"]
        st.rerun()


# --- project detail --------------------------------------------------------


def _render_project_detail(project_path: str) -> None:
    project = _load_project(project_path)
    if project is None:
        st.error(f"Project not found in cache: `{project_path}`")
        if st.button("← Back to overview"):
            st.query_params.clear()
            st.rerun()
        return

    md = project.metadata

    if st.button("← Back to overview"):
        st.query_params.clear()
        st.rerun()

    st.title(project.name)

    metric_cols = st.columns(4)
    if md and md.status:
        emoji = _STATUS_EMOJI.get(md.status.value, "·")
        metric_cols[0].metric("Status", f"{emoji} {md.status.value}")
    metric_cols[1].metric("Type", project.type.value)
    if md and md.branch:
        metric_cols[2].metric("Branch", md.branch)
    if md and md.dirty_count is not None:
        metric_cols[3].metric("Dirty files", md.dirty_count)

    st.divider()

    st.caption(f"📁 `{project.path}`")
    st.caption(f"📦 Umbrella: `{_shorten_home(project.umbrella)}`")
    st.caption(f"🕐 Last modified: {project.last_modified.strftime('%Y-%m-%d %H:%M')}")
    if md and md.last_commit_ts:
        commit_line = f"📝 Last commit: {md.last_commit_ts.strftime('%Y-%m-%d %H:%M')}"
        if md.last_commit_author:
            commit_line += f" by {md.last_commit_author}"
        if md.last_commit_sha:
            commit_line += f" ({md.last_commit_sha[:8]})"
        st.caption(commit_line)

    if md and md.readme_excerpt:
        st.divider()
        st.subheader("README")
        st.info(md.readme_excerpt)

    if md and md.adr_paths:
        st.divider()
        st.subheader(f"Architecture Decision Records ({len(md.adr_paths)})")
        for adr in md.adr_paths:
            st.markdown(f"- `{adr.name}` — `{adr}`")

    st.divider()
    st.subheader("Open in…")
    st.caption("Launcher integration arrives in M5.")


@st.cache_data(ttl=60, show_spinner=False)
def _load_project(project_path: str) -> Project | None:
    """Look up one project by its canonical path. Cached separately from
    the overview rows so navigating into a detail page does not invalidate
    the table list."""
    with Cache() as cache:
        for project in cache.list_projects():
            if str(project.path) == project_path:
                return project
    return None


main()

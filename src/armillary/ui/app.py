"""Streamlit dashboard for armillary.

The dashboard is a **read-only consumer of the SQLite cache**. It never
runs the scanner, never opens GitPython, never reads README files —
that all happens in `armillary scan` from the CLI. Streamlit reruns
the whole script on every interaction, so anything expensive in this
file would tank the dashboard the moment a user with 100+ projects
clicked a filter.

The two views (overview table and per-project detail) live in the same
script and route via `st.query_params["project"]`.

The dashboard does call `git log` (subprocess) on the detail view to
show recent commits, and `ripgrep` (via `armillary.search.LiteralSearch`)
when the user submits a search. Both are user-triggered and bounded —
not in the per-rerun hot path.
"""

from __future__ import annotations

import shutil
import subprocess
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
from armillary import launcher as launcher_mod  # noqa: E402
from armillary.cache import Cache, default_db_path  # noqa: E402
from armillary.config import (  # noqa: E402
    Config,
    ConfigError,
    default_config_path,
    load_config,
)
from armillary.models import Project  # noqa: E402
from armillary.search import LiteralSearch, SearchHit  # noqa: E402

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
    # `dirty_count` stays None when metadata was never extracted (e.g.
    # `armillary scan --no-metadata` or a repo whose extraction failed).
    # Coercing it to 0 would lie — the dashboard would claim the working
    # tree is clean even though it never looked. Let Streamlit's
    # NumberColumn render the absence as an empty cell instead.
    dirty_value = md.dirty_count if md and md.dirty_count is not None else None
    return {
        "Status": status_label,
        "Type": p.type.value,
        "Name": p.name,
        "Branch": (md.branch if md else None) or "—",
        "Dirty": dirty_value,
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
    _render_header_caption()

    rows = _load_overview_rows()
    if not rows:
        st.info(
            "No projects in cache yet. Run `armillary scan -u <path>` from "
            "your terminal to populate the dashboard."
        )
        return

    cfg = _safe_load_config()

    filters = _render_sidebar(rows)
    name_filter = filters.pop("name_substring", "")

    # Top-level search bar — runs ripgrep across cached projects on demand.
    _render_search_section(rows)

    filtered = _apply_filters(rows, filters=filters, search=name_filter)
    _render_summary_metrics(filtered)
    st.divider()

    if not filtered:
        st.warning("No projects match the current filters.")
        return

    _render_table(filtered)
    # cfg may be None if config is malformed; the table doesn't need it
    # but we keep the variable around so future overview-level launcher
    # affordances have a single place to plug in.
    del cfg


def _render_header_caption() -> None:
    """Sub-title line with version, last scan time, and a config link."""
    parts = [f"v{__version__}"]

    # Last scan time = max(last_scanned_at) across all rows. Cheap query.
    try:
        with Cache() as cache:
            row = cache.conn.execute(
                "SELECT MAX(last_scanned_at) FROM projects"
            ).fetchone()
        last_scanned_ts = row[0] if row else None
    except Exception:  # noqa: BLE001 — never let a header crash the page
        last_scanned_ts = None

    if last_scanned_ts:
        from datetime import datetime as _dt

        when = _dt.fromtimestamp(last_scanned_ts).strftime("%Y-%m-%d %H:%M")
        parts.append(f"last scan: {when}")

    parts.append(f"config: `{_shorten_home(default_config_path())}`")
    parts.append(f"cache: `{_shorten_home(default_db_path())}`")

    st.caption(" · ".join(parts))


_SEARCH_STATE_KEY = "armillary_search_state"


def _render_search_section(rows: list[dict[str, Any]]) -> None:
    """Top-level ripgrep search across all cached projects.

    PLAN.md §5: 'Global search bar at the top — first iteration:
    ripgrep literal search'. Form-based so the search only fires
    on submit, not on every keystroke. Results live in
    `st.session_state` so they survive subsequent reruns triggered
    by per-result navigation buttons (otherwise the early-return
    after `submitted` would drop the entire results section on the
    next click and the button events would never fire).
    """
    with st.form("search_form", clear_on_submit=False):
        col_q, col_btn = st.columns([6, 1])
        with col_q:
            query = st.text_input(
                "Search across project files (ripgrep)",
                placeholder="Type a query and press Search…",
                label_visibility="collapsed",
            )
        with col_btn:
            submitted = st.form_submit_button("🔍 Search", use_container_width=True)

    if submitted:
        cleaned = query.strip()
        if not cleaned:
            # Empty submit clears any previous results — gives the user
            # an explicit way to dismiss the section.
            st.session_state.pop(_SEARCH_STATE_KEY, None)
        elif not LiteralSearch.is_available():
            st.session_state[_SEARCH_STATE_KEY] = {
                "query": cleaned,
                "error": (
                    "ripgrep (`rg`) is not on PATH. Install it "
                    "(`brew install ripgrep`) to use search."
                ),
            }
        else:
            backend = LiteralSearch()
            hits_by_project: list[tuple[dict[str, Any], list[SearchHit]]] = []
            with st.spinner(f"Searching {len(rows)} projects for '{cleaned}'…"):
                for row in rows:
                    project_path = Path(row["_path"])
                    try:
                        hits = backend.search(
                            cleaned, root=project_path, max_results=20
                        )
                    except Exception:  # noqa: BLE001 — best effort, skip broken
                        continue
                    if hits:
                        hits_by_project.append((row, hits))
            st.session_state[_SEARCH_STATE_KEY] = {
                "query": cleaned,
                "results": hits_by_project,
            }

    state = st.session_state.get(_SEARCH_STATE_KEY)
    if not state:
        return

    saved_query = state["query"]

    if "error" in state:
        st.error(state["error"])
        return

    hits_by_project = state["results"]
    total_hits = sum(len(h) for _, h in hits_by_project)
    if total_hits == 0:
        st.warning(f"No matches for '{saved_query}'.")
        return

    header_col, clear_col = st.columns([5, 1])
    with header_col:
        st.success(
            f"Found {total_hits} match(es) for '{saved_query}' "
            f"in {len(hits_by_project)} project(s)."
        )
    with clear_col:
        if st.button("✕ Clear", use_container_width=True, key="clear_search"):
            st.session_state.pop(_SEARCH_STATE_KEY, None)
            st.rerun()

    for row, hits in hits_by_project[:10]:
        with st.expander(f"📂 {row['Name']}  ({len(hits)} match(es))"):
            if st.button(
                "→ Open project detail",
                key=f"open_search_hit_{row['_path']}",
            ):
                st.query_params["project"] = row["_path"]
                st.rerun()
            for hit in hits[:10]:
                location = (
                    f"{hit.path}:{hit.line}" if hit.line is not None else str(hit.path)
                )
                st.markdown(f"**`{location}`**")
                st.code(hit.preview, language="text")
    if len(hits_by_project) > 10:
        st.caption(f"…showing top 10 of {len(hits_by_project)} matching projects.")

    st.divider()


def _render_sidebar(rows: list[dict[str, Any]]) -> dict[str, Any]:
    with st.sidebar:
        st.header("Filters")

        statuses = sorted({r["_status_raw"] for r in rows if r["_status_raw"]})
        types = sorted({r["Type"] for r in rows})
        umbrellas = sorted({r["Umbrella"] for r in rows})

        status_pick = st.multiselect("Status", statuses)
        type_pick = st.multiselect("Type", types)
        umbrella_pick = st.multiselect("Umbrella", umbrellas)
        name_substring = st.text_input(
            "Name contains",
            placeholder="quick filter…",
        )

        st.divider()
        st.caption(f"{len(rows)} projects in cache")
        if st.button("🔄 Refresh from cache", use_container_width=True):
            # Both data caches need to be invalidated — otherwise reopening
            # a project detail page after a scan would still show the old
            # branch / status / README for up to a minute.
            _load_overview_rows.clear()
            _load_project.clear()
            st.rerun()

    return {
        "status": status_pick,
        "type": type_pick,
        "umbrella": umbrella_pick,
        "name_substring": name_substring,
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

    _render_detail_metric_tiles(project)

    st.divider()
    _render_detail_captions(project)

    # PLAN.md §5: "Open in…" dropdown wired to launcher catalogue.
    cfg = _safe_load_config()
    if cfg is not None:
        st.divider()
        st.subheader("Open in…")
        _render_launcher_dropdown(project, cfg)

    if md and md.readme_excerpt:
        st.divider()
        st.subheader("README")
        st.info(md.readme_excerpt)

    if project.type.value == "git":
        st.divider()
        st.subheader("Recent commits")
        _render_recent_commits(project.path)

    if md and md.note_paths:
        st.divider()
        st.subheader(f"Notes ({len(md.note_paths)})")
        for note in md.note_paths:
            st.markdown(f"- `{note.name}` — `{note}`")

    if md and md.adr_paths:
        st.divider()
        st.subheader(f"Architecture Decision Records ({len(md.adr_paths)})")
        for adr in md.adr_paths:
            st.markdown(f"- `{adr.name}` — `{adr}`")


def _render_detail_metric_tiles(project: Project) -> None:
    md = project.metadata
    metric_cols = st.columns(4)

    if md and md.status:
        emoji = _STATUS_EMOJI.get(md.status.value, "·")
        metric_cols[0].metric("Status", f"{emoji} {md.status.value}")
    else:
        metric_cols[0].metric("Status", "—")
    metric_cols[1].metric("Type", project.type.value)
    if md and md.branch:
        metric_cols[2].metric("Branch", md.branch)
    if md and md.dirty_count is not None:
        metric_cols[3].metric("Dirty files", md.dirty_count)

    # Second row: ahead / behind / size / file count from PR #10.
    if md and any(
        x is not None for x in (md.ahead, md.behind, md.size_bytes, md.file_count)
    ):
        row2 = st.columns(4)
        if md.ahead is not None:
            row2[0].metric("Ahead", md.ahead)
        if md.behind is not None:
            row2[1].metric("Behind", md.behind)
        if md.size_bytes is not None:
            row2[2].metric("Size", _format_bytes(md.size_bytes))
        if md.file_count is not None:
            row2[3].metric("Files", md.file_count)


def _render_detail_captions(project: Project) -> None:
    md = project.metadata
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


def _render_launcher_dropdown(project: Project, cfg: Config) -> None:
    """PLAN.md §5: '"Open in…" dropdown per project — driven by yaml config'.

    Each non-terminal entry from `cfg.launchers` is shown with its
    label/icon. Click → calls `launcher.launch()` and surfaces
    success/error inline.

    **Terminal launchers are excluded** from the dashboard. Their
    `subprocess.run()` path inherits the parent's stdio (necessary
    for interactive `codex` / `claude-code` sessions) which would
    block the Streamlit server thread and commandeer the terminal
    that hosts the dashboard process. Use them from the CLI instead
    via `armillary open <name> -t <target>`.
    """
    if not cfg.launchers:
        st.caption("No launchers configured.")
        return

    available_targets: list[tuple[str, str]] = []
    missing_labels: list[str] = []
    terminal_only_labels: list[str] = []
    for target_id, launcher_cfg in cfg.launchers.items():
        label = (
            f"{launcher_cfg.icon + ' ' if launcher_cfg.icon else ''}"
            f"{launcher_cfg.label}"
        )
        if launcher_cfg.terminal:
            terminal_only_labels.append(launcher_cfg.label)
            continue
        if shutil.which(launcher_cfg.command) is not None:
            available_targets.append((target_id, label))
        else:
            missing_labels.append(label)

    if not available_targets:
        st.warning(
            "No GUI launcher executables found on PATH. Edit "
            f"`{_shorten_home(default_config_path())}` to add one."
        )
        if missing_labels:
            st.caption(f"Configured but missing: {', '.join(missing_labels)}")
        if terminal_only_labels:
            st.caption(
                f"Terminal-only launchers (CLI: `armillary open <name> -t <id>`): "
                f"{', '.join(terminal_only_labels)}"
            )
        return

    col_select, col_btn = st.columns([3, 1])
    with col_select:
        target_id = st.selectbox(
            "Launcher",
            options=[t[0] for t in available_targets],
            format_func=lambda tid: dict(available_targets)[tid],
            label_visibility="collapsed",
            key=f"launcher_pick_{project.path}",
        )
    with col_btn:
        clicked = st.button(
            "🚀 Open",
            use_container_width=True,
            key=f"launcher_open_{project.path}",
        )

    if clicked:
        result = launcher_mod.launch(project, target_id, launchers=cfg.launchers)
        if result.ok:
            st.success(f"Opened in `{target_id}`.")
        else:
            st.error(result.error or "Launch failed.")

    if missing_labels:
        st.caption(f"Not on PATH (skipped): {', '.join(missing_labels)}")
    if terminal_only_labels:
        st.caption(
            f"Terminal-only launchers (CLI: `armillary open <name> -t <id>`): "
            f"{', '.join(terminal_only_labels)}"
        )


def _render_recent_commits(repo_path: Path, limit: int = 5) -> None:
    """Show the last `limit` commits as a markdown list.

    Calls `git log` directly via subprocess — much cheaper than going
    through GitPython for a one-off display, and we already have a
    timeout pattern from `LiteralSearch`.
    """
    commits = _git_log_recent(repo_path, limit=limit)
    if not commits:
        st.caption("_No commit history available._")
        return

    for commit in commits:
        st.markdown(
            f"- **`{commit['sha']}`** — {commit['message']}  \n"
            f"  _{commit['date']} · {commit['author']}_"
        )


def _git_log_recent(repo_path: Path, *, limit: int = 5) -> list[dict[str, str]]:
    try:
        proc = subprocess.run(
            [
                "git",
                "log",
                f"-{limit}",
                "--no-merges",
                "--format=%h\x1f%s\x1f%ci\x1f%an",
            ],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []

    commits: list[dict[str, str]] = []
    for line in proc.stdout.strip().splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        commits.append(
            {
                "sha": parts[0],
                "message": parts[1],
                "date": parts[2],
                "author": parts[3],
            }
        )
    return commits


def _format_bytes(n: int) -> str:
    """Format `n` bytes as KB / MB / GB with one decimal place."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"


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
        for project in cache.list_projects():
            if str(project.path) == project_path:
                return project
    return None


main()

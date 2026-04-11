"""Streamlit dashboard for armillary.

The dashboard is a **read-only consumer of the SQLite cache** for every
rerender. The hot path (filter click, search submit, page navigation)
never walks the filesystem or opens GitPython — that work is bounded
to the explicit "Scan now" button and the per-detail-page `git log`.
Streamlit reruns the whole script on every interaction; anything
expensive in the rerender path would tank a 100+ project view.

The two views (overview table and per-project detail) live in the same
script and route via `st.query_params["project"]`.
"""

from __future__ import annotations

import contextlib
import shlex
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
from armillary import metadata as metadata_mod  # noqa: E402
from armillary import status as status_mod  # noqa: E402
from armillary.cache import Cache, default_db_path  # noqa: E402
from armillary.config import (  # noqa: E402
    Config,
    ConfigError,
    KhojConfigBlock,
    LauncherConfig,
    UmbrellaConfig,
    default_config_path,
    load_config,
    write_config,
)
from armillary.models import Project, ProjectType, UmbrellaFolder  # noqa: E402
from armillary.scanner import scan as scan_umbrellas_fn  # noqa: E402
from armillary.search import (  # noqa: E402
    KhojConfig,
    KhojSearch,
    LiteralSearch,
    SearchHit,
)

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


# --- shared scan operation -------------------------------------------------


def _run_dashboard_scan(cfg: Config | None) -> tuple[bool, str]:
    """Walk the configured umbrellas, persist to cache, return status.

    Mirrors the pipeline used by `cli.scan` and PR #16's
    `_run_initial_scan_and_summary`: scanner → metadata → status compute
    (with the `last_modified = max(fs, last_commit_ts)` lift for git
    repos) → cache.upsert. Then clears Streamlit's data caches so the
    rerender shows the fresh data.

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
        projects = scan_umbrellas_fn(umbrellas)
        metadata_mod.extract_all(projects)
        for project in projects:
            if project.metadata is None:
                continue
            if (
                project.type is ProjectType.GIT
                and project.metadata.last_commit_ts is not None
                and project.metadata.last_commit_ts > project.last_modified
            ):
                project.last_modified = project.metadata.last_commit_ts
            project.metadata.status = status_mod.compute_status(project)

        with Cache() as cache:
            cache.upsert(projects, write_metadata=True)
            cache.prune_stale()
    except Exception as exc:  # noqa: BLE001 — surface error to the UI, not crash
        return False, f"Scan failed: {exc}"

    # Both data caches must be cleared BEFORE the rerun so the next
    # rerender reads fresh rows instead of the 60-second-stale TTL.
    _load_overview_rows.clear()
    _load_project.clear()

    return True, f"Scanned {len(projects)} project(s)."


# --- routing ---------------------------------------------------------------


def main() -> None:
    params = st.query_params
    page = params.get("page")
    project_path = params.get("project")
    if page == "settings":
        _render_settings_page()
    elif project_path:
        _render_project_detail(project_path)
    else:
        _render_overview()


# --- overview --------------------------------------------------------------


def _render_overview() -> None:
    st.title("🔭 armillary")
    _render_header_caption()

    cfg = _safe_load_config()
    rows = _load_overview_rows()

    if not rows:
        _render_empty_cache_state(cfg)
        return

    filters = _render_sidebar(rows, cfg)
    name_filter = filters.pop("name_substring", "")

    # Top-level search bar — runs ripgrep across cached projects on demand.
    _render_search_section(rows, cfg)

    filtered = _apply_filters(rows, filters=filters, search=name_filter)
    _render_summary_metrics(filtered)
    st.divider()

    if not filtered:
        st.warning("No projects match the current filters.")
        return

    _render_table(filtered)


def _render_empty_cache_state(cfg: Config | None) -> None:
    """Show a friendly first-launch screen with a real "Scan now" button.

    Replaces the old text hint that told the user to "go to the terminal" —
    that violated the rule "what you can't click in the UI doesn't exist".
    """
    st.subheader("🪐 Cache is empty")
    st.write(
        "armillary needs to walk the filesystem at least once to discover "
        "your projects. Click the button below to run a scan against the "
        "umbrellas in your config."
    )

    can_scan = cfg is not None and bool(cfg.umbrellas)

    col_btn, col_help = st.columns([1, 2])
    with col_btn:
        if st.button(
            "🔁 Scan filesystem now",
            use_container_width=True,
            disabled=not can_scan,
            key="empty_state_scan",
        ):
            with st.spinner("Scanning…"):
                ok, message = _run_dashboard_scan(cfg)
            if ok:
                st.success(message)
                st.rerun()
            else:
                st.error(message)
    with col_help:
        if can_scan:
            st.caption(
                f"Will scan: {', '.join(_shorten_home(u.path) for u in cfg.umbrellas)}"
            )
        else:
            st.warning(
                "No umbrellas configured. Run `armillary config --init` "
                "from your terminal first."
            )


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


def _render_search_section(rows: list[dict[str, Any]], cfg: Config | None) -> None:
    """Top-level search across all cached projects.

    PLAN.md §5: 'Global search bar at the top — first iteration:
    ripgrep literal search; second iteration: Khoj semantic search'.
    Form-based so the search only fires on submit, not on every
    keystroke. Results live in `st.session_state` so they survive
    subsequent reruns triggered by per-result navigation buttons
    (otherwise the early-return after `submitted` would drop the
    entire results section on the next click and the button events
    would never fire — PR #11 mid-PR fix).

    Controls:
    - text input for the query
    - dropdown to restrict to one project (optional)
    - Khoj toggle (only when `cfg.khoj.enabled` is True)
    - max hits number input (1..500, default 50)
    - Search submit button
    """
    project_options = ["(all projects)"] + sorted(r["Name"] for r in rows)
    khoj_available = cfg is not None and cfg.khoj.enabled

    with st.form("search_form", clear_on_submit=False):
        # Row 1: query + Search button
        col_q, col_btn = st.columns([6, 1])
        with col_q:
            query = st.text_input(
                "Search across project files",
                placeholder="Type a query and press Search…",
                label_visibility="collapsed",
            )
        with col_btn:
            submitted = st.form_submit_button("🔍 Search", use_container_width=True)

        # Row 2: filters / options
        opt_cols = st.columns([3, 2, 2] if khoj_available else [3, 2, 2])
        with opt_cols[0]:
            project_pick = st.selectbox(
                "Restrict to project",
                project_options,
                index=0,
            )
        with opt_cols[1]:
            max_hits = st.number_input(
                "Max hits",
                min_value=1,
                max_value=500,
                value=50,
                step=10,
            )
        with opt_cols[2]:
            if khoj_available:
                use_khoj = st.checkbox(
                    "🧠 Semantic (Khoj)",
                    value=False,
                    help=(
                        "Use Khoj's semantic search instead of ripgrep. "
                        "Falls back to ripgrep on any Khoj failure."
                    ),
                )
            else:
                use_khoj = False
                st.caption("_Khoj disabled in config_")

    if submitted:
        cleaned = query.strip()
        if not cleaned:
            # Empty submit clears any previous results — gives the user
            # an explicit way to dismiss the section.
            st.session_state.pop(_SEARCH_STATE_KEY, None)
        else:
            _run_search_and_store(
                rows=rows,
                cfg=cfg,
                query=cleaned,
                project_pick=(
                    None if project_pick == "(all projects)" else project_pick
                ),
                max_hits=int(max_hits),
                use_khoj=use_khoj,
            )

    _render_search_results()


def _run_search_and_store(
    *,
    rows: list[dict[str, Any]],
    cfg: Config | None,
    query: str,
    project_pick: str | None,
    max_hits: int,
    use_khoj: bool,
) -> None:
    """Execute the chosen search backend, persist results to session state.

    Stores under `_SEARCH_STATE_KEY` so subsequent reruns (triggered by
    "Open project detail" buttons) can keep rendering the results
    without re-querying.

    Two backend-specific subtleties (Codex review on PR #17):

    1. **Per-call max_results cap differs by backend.** Ripgrep is
       genuinely per-project — capping each call at the remaining
       budget saves work and is correct. Khoj caches its GLOBAL
       response per `(query, max_results)` and then post-filters by
       root, so passing different `max_results` each iteration would
       break the cache (re-querying Khoj each time) AND drop valid
       matches from later projects (because each subsequent call
       queries a smaller global window). For Khoj we always pass the
       full `max_hits` and clamp the displayed slice afterwards.

    2. **Per-project failures vs backend-wide failures.** A ripgrep
       failure on one project is a per-project issue (broken file,
       permission error) — continue to the next. A Khoj failure
       (URL error, malformed response, KhojResponseError when no
       fallback) is backend-wide — surface it as a search error
       instead of pretending the search returned zero matches.
    """
    backend, backend_label, error = _build_dashboard_search_backend(cfg, use_khoj)
    if error is not None:
        st.session_state[_SEARCH_STATE_KEY] = {"query": query, "error": error}
        return

    # Restrict the projects we iterate to either the selected one or all.
    if project_pick is not None:
        target_rows = [r for r in rows if r["Name"] == project_pick]
    else:
        target_rows = rows

    hits_by_project: list[tuple[dict[str, Any], list[SearchHit]]] = []
    total_hits = 0
    with st.spinner(f"Searching {len(target_rows)} project(s) for '{query}'…"):
        for row in target_rows:
            if total_hits >= max_hits:
                break
            project_path = Path(row["_path"])

            # Khoj: always pass the full cap so the per-query cache fires
            # once. Ripgrep: cap to remaining budget for efficiency.
            per_call_cap = max_hits if use_khoj else (max_hits - total_hits)

            try:
                hits = backend.search(
                    query,
                    root=project_path,
                    max_results=per_call_cap,
                )
            except Exception as exc:  # noqa: BLE001 — see docstring
                if use_khoj:
                    # Backend-wide failure: do NOT silently keep going,
                    # the user will think "search returned no matches"
                    # when actually the backend never answered.
                    st.session_state[_SEARCH_STATE_KEY] = {
                        "query": query,
                        "error": (
                            f"Semantic search backend failed: {exc}. "
                            "Check that Khoj is running on "
                            f"{cfg.khoj.api_url if cfg else 'the configured URL'}."
                        ),
                    }
                    return
                # ripgrep per-project error — broken file / perms / etc.
                # Skip this project and try the next.
                continue

            if hits:
                # Cap displayed hits to the remaining budget so we don't
                # overshoot max_hits if a single project returns more
                # than the cap (Khoj global → root post-filter case).
                room = max_hits - total_hits
                hits_to_keep = hits[:room]
                if hits_to_keep:
                    hits_by_project.append((row, hits_to_keep))
                    total_hits += len(hits_to_keep)

    st.session_state[_SEARCH_STATE_KEY] = {
        "query": query,
        "results": hits_by_project,
        "backend": backend_label,
        "max_hits": max_hits,
    }


def _build_dashboard_search_backend(
    cfg: Config | None, use_khoj: bool
) -> tuple[Any, str, str | None]:
    """Pick a backend for the search submit, with the same fallback
    semantics as `cli.search` but adapted to the dashboard:

    - When `use_khoj` is False: LiteralSearch (ripgrep). Returns an
      error string if `rg` is missing.
    - When `use_khoj` is True: KhojSearch with LiteralSearch fallback
      if `rg` is also available, or no fallback otherwise.

    Returns `(backend, label_for_banner, error_or_None)`.
    """
    if not use_khoj:
        if not LiteralSearch.is_available():
            return (
                None,
                "ripgrep",
                "ripgrep (`rg`) is not on PATH. Install it (`brew install "
                "ripgrep`) to use literal search.",
            )
        return LiteralSearch(), "ripgrep", None

    # Khoj path. cfg is guaranteed non-None+enabled here because the
    # checkbox only renders when khoj is enabled.
    assert cfg is not None
    fallback: LiteralSearch | None = (
        LiteralSearch() if LiteralSearch.is_available() else None
    )
    backend = KhojSearch(
        config=KhojConfig(
            api_url=cfg.khoj.api_url,
            api_key=cfg.khoj.api_key,
            timeout_seconds=cfg.khoj.timeout_seconds,
        ),
        fallback=fallback,
    )
    return backend, "semantic (Khoj)", None


def _render_search_results() -> None:
    """Render the persisted search results from session state.

    Lives in its own function (not inside `_render_search_section`)
    so the rendering pass runs on EVERY rerun — not only on the
    rerun where the form was submitted. That's the PR #11 mid-PR fix
    for "results disappear when you click a per-result button".
    """
    state = st.session_state.get(_SEARCH_STATE_KEY)
    if not state:
        return

    saved_query = state["query"]

    if "error" in state:
        st.error(state["error"])
        return

    hits_by_project = state["results"]
    backend_label = state.get("backend", "ripgrep")
    total_hits = sum(len(h) for _, h in hits_by_project)
    if total_hits == 0:
        st.warning(f"No matches for '{saved_query}' ({backend_label}).")
        return

    header_col, clear_col = st.columns([5, 1])
    with header_col:
        st.success(
            f"Found {total_hits} match(es) for '{saved_query}' "
            f"in {len(hits_by_project)} project(s) ({backend_label})."
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


def _render_sidebar(rows: list[dict[str, Any]], cfg: Config | None) -> dict[str, Any]:
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

        # Two distinct refresh paths:
        #   "Reload from cache" — cheap, just rereads SQLite (clears the
        #   60-second TTL on st.cache_data). Use after `armillary scan`
        #   from a terminal.
        #   "Scan filesystem now" — expensive, walks the umbrellas, runs
        #   metadata extraction, computes status, persists to cache.
        if st.button(
            "🔄 Reload from cache",
            use_container_width=True,
            key="sidebar_reload",
        ):
            _load_overview_rows.clear()
            _load_project.clear()
            st.rerun()

        scan_disabled = cfg is None or not cfg.umbrellas
        scan_help = None
        if scan_disabled:
            scan_help = "No umbrellas in config. Run `armillary config --init`."
        if st.button(
            "🔁 Scan filesystem now",
            use_container_width=True,
            disabled=scan_disabled,
            help=scan_help,
            key="sidebar_scan",
        ):
            with st.spinner("Scanning…"):
                ok, message = _run_dashboard_scan(cfg)
            if ok:
                st.success(message)
                st.rerun()
            else:
                st.error(message)

        st.divider()
        if st.button(
            "⚙️ Settings",
            use_container_width=True,
            key="sidebar_settings",
        ):
            st.query_params["page"] = "settings"
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


# --- settings page ---------------------------------------------------------


def _render_settings_page() -> None:
    """In-UI editor for the YAML config — umbrellas, launchers, Khoj.

    Replaces the "edit YAML by hand" workflow per the user-stated rule
    "what you can't click in the UI doesn't exist". Three tabs, each
    with its own form + Save button. Inline test affordances for the
    things that can be tested without leaving the page (launcher PATH
    check, Khoj health probe).

    Loading the page itself is read-only — the only filesystem writes
    happen on explicit "Save" button clicks.
    """
    if st.button("← Back to overview", key="settings_back"):
        with contextlib.suppress(KeyError):
            del st.query_params["page"]
        st.rerun()

    st.title("⚙️ Settings")
    st.caption(f"Editing `{_shorten_home(default_config_path())}`")

    try:
        cfg = load_config()
    except ConfigError as exc:
        st.error(f"Config could not be loaded:\n\n```\n{exc}\n```")
        st.info(
            "Fix the YAML by hand (`armillary config` from a terminal), "
            "then click Reload below."
        )
        if st.button("🔄 Reload config"):
            st.rerun()
        return

    tabs = st.tabs(["Umbrellas", "Launchers", "Khoj"])
    with tabs[0]:
        _render_settings_umbrellas(cfg)
    with tabs[1]:
        _render_settings_launchers(cfg)
    with tabs[2]:
        _render_settings_khoj(cfg)


# ----- Umbrellas tab -------------------------------------------------------


def _render_settings_umbrellas(cfg: Config) -> None:
    st.subheader("Umbrellas")
    st.caption(
        "Folders the scanner walks. Each entry becomes a `-u` argument "
        "for `armillary scan`."
    )

    edited: list[UmbrellaConfig] = []

    if not cfg.umbrellas:
        st.info(
            "_No umbrellas configured. Add one below or run "
            "`armillary config --init` from your terminal._"
        )

    for idx, umbrella in enumerate(cfg.umbrellas):
        cols = st.columns([4, 2, 1, 1])
        with cols[0]:
            new_path = st.text_input(
                "Path",
                value=str(umbrella.path),
                key=f"umbrella_path_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
        with cols[1]:
            new_label = st.text_input(
                "Label",
                value=umbrella.label or "",
                key=f"umbrella_label_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
        with cols[2]:
            new_depth = st.number_input(
                "Max depth",
                min_value=1,
                max_value=10,
                value=umbrella.max_depth,
                key=f"umbrella_depth_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
        with cols[3]:
            # Spacer for vertical alignment with the input rows above
            if idx == 0:
                st.write("")
            remove = st.button(
                "✕",
                key=f"umbrella_remove_{idx}",
                help="Remove this umbrella",
            )
        if not remove:
            edited.append(
                UmbrellaConfig(
                    path=Path(new_path),
                    label=new_label or None,
                    max_depth=int(new_depth),
                )
            )

    st.divider()
    st.markdown("**Add umbrella**")
    add_cols = st.columns([4, 2, 1, 1])
    with add_cols[0]:
        new_path = st.text_input(
            "New path",
            value="",
            placeholder="~/Projects",
            key="umbrella_add_path",
        )
    with add_cols[1]:
        new_label = st.text_input(
            "New label",
            value="",
            placeholder="(auto)",
            key="umbrella_add_label",
        )
    with add_cols[2]:
        new_depth = st.number_input(
            "Depth",
            min_value=1,
            max_value=10,
            value=3,
            key="umbrella_add_depth",
        )
    with add_cols[3]:
        st.write("")
        add_clicked = st.button("Add", key="umbrella_add_btn")

    if add_clicked:
        if not new_path.strip():
            st.error("Path cannot be empty.")
        else:
            expanded = Path(new_path).expanduser()
            label = new_label.strip() or expanded.name
            edited.append(
                UmbrellaConfig(
                    path=expanded,
                    label=label,
                    max_depth=int(new_depth),
                )
            )
            cfg.umbrellas = edited
            _save_settings(cfg)
            return

    st.divider()
    if st.button("💾 Save changes", key="umbrellas_save", type="primary"):
        cfg.umbrellas = edited
        _save_settings(cfg)


# ----- Launchers tab -------------------------------------------------------


def _render_settings_launchers(cfg: Config) -> None:
    st.subheader("Launchers")
    st.caption(
        "Tools `armillary open` can spawn. Built-in entries are always "
        "available even when removed from this list — they reappear "
        "after a save."
    )

    edited: dict[str, LauncherConfig] = {}

    for target_id in sorted(cfg.launchers.keys()):
        launcher = cfg.launchers[target_id]
        on_path = shutil.which(launcher.command) is not None
        status = "🟢 on PATH" if on_path else "🔴 missing"

        with st.expander(f"{launcher.icon or '·'} {target_id} — {status}"):
            cols_top = st.columns([3, 3, 1, 1])
            with cols_top[0]:
                new_label = st.text_input(
                    "Label",
                    value=launcher.label,
                    key=f"launcher_label_{target_id}",
                )
            with cols_top[1]:
                new_command = st.text_input(
                    "Command",
                    value=launcher.command,
                    key=f"launcher_command_{target_id}",
                )
            with cols_top[2]:
                new_icon = st.text_input(
                    "Icon",
                    value=launcher.icon or "",
                    key=f"launcher_icon_{target_id}",
                )
            with cols_top[3]:
                new_terminal = st.checkbox(
                    "Terminal",
                    value=launcher.terminal,
                    key=f"launcher_terminal_{target_id}",
                    help=(
                        "Mark interactive terminal apps (codex, claude-code) "
                        "so the dashboard hides them and the CLI keeps stdio."
                    ),
                )

            new_args = st.text_input(
                "Args (space-separated, use `{path}` for project path)",
                value=" ".join(shlex.quote(a) for a in launcher.args),
                key=f"launcher_args_{target_id}",
            )

            cols_bottom = st.columns([1, 1, 4])
            with cols_bottom[0]:
                test_clicked = st.button(
                    "🧪 Test",
                    key=f"launcher_test_{target_id}",
                    help="Check whether the command is on PATH (no spawn)",
                )
            with cols_bottom[1]:
                remove_clicked = st.button(
                    "✕ Remove",
                    key=f"launcher_remove_{target_id}",
                )

            if test_clicked:
                resolved = shutil.which(new_command)
                if resolved:
                    st.success(f"Found: `{resolved}`")
                else:
                    st.error(
                        f"`{new_command}` is not on PATH. Install it or "
                        "fix the command above."
                    )

            if not remove_clicked:
                try:
                    parsed_args = shlex.split(new_args) if new_args.strip() else []
                except ValueError as exc:
                    st.error(f"Could not parse args: {exc}")
                    parsed_args = launcher.args
                edited[target_id] = LauncherConfig(
                    label=new_label,
                    command=new_command,
                    args=parsed_args,
                    icon=new_icon or None,
                    terminal=new_terminal,
                )

    st.divider()
    st.markdown("**Add custom launcher**")
    with st.form("launcher_add_form", clear_on_submit=True):
        a_cols = st.columns([2, 3, 3])
        with a_cols[0]:
            new_id = st.text_input("ID", placeholder="nvim")
        with a_cols[1]:
            new_label = st.text_input("Label", placeholder="Neovim")
        with a_cols[2]:
            new_command = st.text_input("Command", placeholder="nvim")

        b_cols = st.columns([4, 2, 1])
        with b_cols[0]:
            new_args = st.text_input("Args", value="{path}", placeholder="{path}")
        with b_cols[1]:
            new_icon = st.text_input("Icon", value="", placeholder="✏️")
        with b_cols[2]:
            new_terminal = st.checkbox("Terminal", value=False)

        add_clicked = st.form_submit_button("Add")

    if add_clicked:
        cleaned_id = new_id.strip()
        if not cleaned_id:
            st.error("ID cannot be empty.")
        elif cleaned_id in edited:
            st.error(f"Launcher id `{cleaned_id}` already exists.")
        elif not new_command.strip():
            st.error("Command cannot be empty.")
        else:
            try:
                parsed_args = shlex.split(new_args) if new_args.strip() else []
            except ValueError as exc:
                st.error(f"Could not parse args: {exc}")
                return
            edited[cleaned_id] = LauncherConfig(
                label=new_label.strip() or cleaned_id,
                command=new_command.strip(),
                args=parsed_args,
                icon=new_icon or None,
                terminal=new_terminal,
            )
            cfg.launchers = edited
            _save_settings(cfg)
            return

    st.divider()
    if st.button("💾 Save changes", key="launchers_save", type="primary"):
        cfg.launchers = edited
        _save_settings(cfg)


# ----- Khoj tab ------------------------------------------------------------


def _render_settings_khoj(cfg: Config) -> None:
    st.subheader("Khoj semantic search")
    st.caption(
        "Optional. When enabled, the search bar gets a 🧠 Semantic toggle "
        "that calls a local Khoj instance instead of ripgrep."
    )

    enabled = st.checkbox(
        "Enable Khoj",
        value=cfg.khoj.enabled,
        key="khoj_enabled",
    )
    api_url = st.text_input(
        "API URL",
        value=cfg.khoj.api_url,
        key="khoj_api_url",
    )
    api_key = st.text_input(
        "API key (optional, sent as Bearer token)",
        value=cfg.khoj.api_key or "",
        type="password",
        key="khoj_api_key",
    )
    timeout_seconds = st.slider(
        "Timeout (seconds)",
        min_value=0.5,
        max_value=60.0,
        value=cfg.khoj.timeout_seconds,
        step=0.5,
        key="khoj_timeout",
    )

    cols = st.columns([1, 1, 4])
    with cols[0]:
        test_clicked = st.button("🧪 Test connection", key="khoj_test")
    with cols[1]:
        save_clicked = st.button("💾 Save changes", key="khoj_save", type="primary")

    if test_clicked:
        _test_khoj_connection(api_url, api_key, timeout_seconds)

    if save_clicked:
        cfg.khoj = KhojConfigBlock(
            enabled=enabled,
            api_url=api_url,
            api_key=api_key or None,
            timeout_seconds=timeout_seconds,
        )
        _save_settings(cfg)


def _test_khoj_connection(api_url: str, api_key: str, timeout: float) -> None:
    """Probe `<api_url>/api/health` with the form-supplied timeout.

    Treats any 2xx status as success. Catches the same exception family
    as the CLI's init Khoj-detect step plus `KhojResponseError` for
    safety.
    """
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    try:
        url = f"{api_url.rstrip('/')}/api/health"
        request = Request(url)
        if api_key:
            request.add_header("Authorization", f"Bearer {api_key}")
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            if 200 <= int(status) < 300:
                st.success(f"Khoj responded with HTTP {status}.")
            else:
                st.error(f"Khoj responded with HTTP {status}.")
    except HTTPError as exc:
        st.error(f"Khoj returned HTTP {exc.code}: {exc.reason}")
    except (URLError, TimeoutError, OSError) as exc:
        st.error(f"Khoj unreachable at {api_url}: {exc}")
    except Exception as exc:  # noqa: BLE001 — surface anything weird
        st.error(f"Khoj test failed: {exc}")


# ----- save helper ---------------------------------------------------------


def _save_settings(cfg: Config) -> None:
    """Persist `cfg` to YAML, clear data caches, rerun.

    Called by every "💾 Save changes" button. Streamlit reruns from the
    top after `st.rerun()`, so the user lands on the freshly-saved view.
    """
    try:
        write_config(cfg)
    except OSError as exc:
        st.error(f"Could not write config: {exc}")
        return
    _load_overview_rows.clear()
    _load_project.clear()
    st.success("Saved.")
    st.rerun()


main()

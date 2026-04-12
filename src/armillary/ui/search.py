"""Global search section — ripgrep literal + Khoj semantic backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import streamlit as st

from armillary.config import Config
from armillary.search import (
    KhojConfig,
    KhojSearch,
    LiteralSearch,
    SearchHit,
)
from armillary.ui.helpers import OverviewRow

_SEARCH_STATE_KEY = "armillary_search_state"


@dataclass
class SearchResults:
    """Typed container for a successful search stored in session state."""

    query: str
    backend_label: str
    hits_by_project: list[tuple[OverviewRow, list[SearchHit]]] = field(
        default_factory=list,
    )
    total_hits: int = 0


@dataclass
class SearchError:
    """Typed container for a failed search stored in session state."""

    query: str
    error: str


def _render_search_section(rows: list[OverviewRow], cfg: Config | None) -> None:
    """Top-level search across all cached projects.

    PLAN.md S5: 'Global search bar at the top — first iteration:
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
    project_options = ["(all projects)"] + sorted(r.name for r in rows)
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
        opt_cols = st.columns([3, 2, 2])
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
                st.markdown(
                    "Semantic search off — [enable in ⚙️ Settings](/?page=settings)",
                    help="Requires a running Khoj server.",
                )

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
    rows: list[OverviewRow],
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
        st.session_state[_SEARCH_STATE_KEY] = SearchError(query=query, error=error)
        return

    # Restrict the projects we iterate to either the selected one or all.
    if project_pick is not None:
        target_rows = [r for r in rows if r.name == project_pick]
    else:
        target_rows = rows

    hits_by_project: list[tuple[OverviewRow, list[SearchHit]]] = []
    total_hits = 0
    with st.spinner(f"Searching {len(target_rows)} project(s) for '{query}'…"):
        for row in target_rows:
            if total_hits >= max_hits:
                break
            project_path = Path(row.path)

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
                    st.session_state[_SEARCH_STATE_KEY] = SearchError(
                        query=query,
                        error=(
                            f"Semantic search backend failed: {exc}. "
                            "Check that Khoj is running on "
                            f"{cfg.khoj.api_url if cfg else 'the configured URL'}."
                        ),
                    )
                    return
                # ripgrep per-project error — broken file / perms / etc.
                # Skip this project and try the next.
                continue

            if hits:
                # Cap displayed hits to the remaining budget so we don't
                # overshoot max_hits if a single project returns more
                # than the cap (Khoj global -> root post-filter case).
                room = max_hits - total_hits
                hits_to_keep = hits[:room]
                if hits_to_keep:
                    hits_by_project.append((row, hits_to_keep))
                    total_hits += len(hits_to_keep)

    st.session_state[_SEARCH_STATE_KEY] = SearchResults(
        query=query,
        backend_label=backend_label,
        hits_by_project=hits_by_project,
        total_hits=total_hits,
    )


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
    if state is None:
        return

    if isinstance(state, SearchError):
        st.error(state.error)
        return

    if not isinstance(state, SearchResults):
        return
    saved_query = state.query
    hits_by_project = state.hits_by_project
    backend_label = state.backend_label
    total_hits = sum(len(h) for _, h in hits_by_project)
    if total_hits == 0:
        st.warning(
            f"No matches for '{saved_query}' ({backend_label}).\n\n"
            "Try a different query, run `armillary scan` to refresh the "
            "index, or check that all umbrella folders are configured."
        )
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
        with st.expander(f"📂 {row.name}  ({len(hits)} match(es))"):
            if st.button(
                "→ Open project detail",
                key=f"open_search_hit_{row.path}",
            ):
                st.query_params["project"] = row.path
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

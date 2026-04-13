"""Global search section — ripgrep literal search backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st

from armillary.config import Config
from armillary.search import LiteralSearch, SearchHit
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

    Global search bar at the top using ripgrep literal search.
    Form-based so the search only fires on submit, not on every
    keystroke. Results live in `st.session_state` so they survive
    subsequent reruns triggered by per-result navigation buttons
    (otherwise the early-return after `submitted` would drop the
    entire results section on the next click and the button events
    would never fire — PR #11 mid-PR fix).

    Controls:
    - text input for the query
    - dropdown to restrict to one project (optional)
    - max hits number input (1..500, default 50)
    - Search submit button
    """
    project_options = ["(all projects)"] + sorted(r.name for r in rows)

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
            submitted = st.form_submit_button(
                "Search",
                icon=":material/search:",
                width="stretch",
            )

        # Row 2: filters / options
        opt_cols = st.columns([3, 2])
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

    if submitted:
        cleaned = query.strip()
        if not cleaned:
            # Empty submit clears any previous results — gives the user
            # an explicit way to dismiss the section.
            st.session_state.pop(_SEARCH_STATE_KEY, None)
        else:
            _run_search_and_store(
                rows=rows,
                query=cleaned,
                project_pick=(
                    None if project_pick == "(all projects)" else project_pick
                ),
                max_hits=int(max_hits),
            )

    _render_search_results()


def _run_search_and_store(
    *,
    rows: list[OverviewRow],
    query: str,
    project_pick: str | None,
    max_hits: int,
) -> None:
    """Execute ripgrep search, persist results to session state.

    Stores under `_SEARCH_STATE_KEY` so subsequent reruns (triggered by
    "Open project detail" buttons) can keep rendering the results
    without re-querying.
    """
    if not LiteralSearch.is_available():
        st.session_state[_SEARCH_STATE_KEY] = SearchError(
            query=query,
            error=(
                "ripgrep (`rg`) is not on PATH. Install it "
                "(`brew install ripgrep`) to use search."
            ),
        )
        return

    backend = LiteralSearch()

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

            try:
                hits = backend.search(
                    query,
                    root=project_path,
                    max_results=max_hits - total_hits,
                )
            except Exception:  # noqa: BLE001 — broken file / perms / etc.
                # Per-project error — skip and try the next.
                continue

            if hits:
                room = max_hits - total_hits
                hits_to_keep = hits[:room]
                if hits_to_keep:
                    hits_by_project.append((row, hits_to_keep))
                    total_hits += len(hits_to_keep)

    st.session_state[_SEARCH_STATE_KEY] = SearchResults(
        query=query,
        backend_label="ripgrep",
        hits_by_project=hits_by_project,
        total_hits=total_hits,
    )


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
            f"in {len(hits_by_project)} project(s) ({backend_label}).",
            icon=":material/check_circle:",
        )
    with clear_col:
        if st.button(
            "Clear",
            icon=":material/close:",
            width="stretch",
            key="clear_search",
        ):
            st.session_state.pop(_SEARCH_STATE_KEY, None)
            st.rerun()

    for row, hits in hits_by_project[:10]:
        with st.expander(
            f"{row.name}  ({len(hits)} match(es))",
            icon=":material/folder:",
        ):
            if st.button(
                "Open project detail",
                icon=":material/arrow_forward:",
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
        st.caption(f"\u2026showing top 10 of {len(hits_by_project)} matching projects.")

"""Steal page — cross-repo ranked code search (ADR 0027).

Entrypoint: ``?page=steal``. Thin presentation over
``steal_service.steal()``. Results render as cards with project
name, status, path, score, symbol, and a syntax-highlighted block.
A copy-button writes the block into the user's clipboard via
``pbcopy`` (macOS) and logs the take to the Steal journal.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from armillary.feedback_service import record_vote
from armillary.steal_service import StealResult, steal
from armillary.transition_service import record_steal
from armillary.ui.sidebar import _render_nav_sidebar

_STATE_KEY = "armillary_steal_state"


@dataclass
class StealResults:
    """Persisted results of the last Steal query (survives reruns)."""

    query: str
    language: str | None
    limit: int
    hits: list[StealResult]


def _render_steal_page() -> None:
    """Top-level Steal page: sidebar + form + results."""
    _render_nav_sidebar()

    st.header(":material/content_copy: Steal — reuse code from your own repos")
    st.caption(
        "Ranked cross-repo search over every file indexed by armillary. "
        "Heuristic ranking: recency × project status. Thumb the ones you "
        "actually take — your votes tune future rankings."
    )

    _render_query_form()
    _render_results()


def _render_query_form() -> None:
    """Query input form. Submit stores results in session state."""
    with st.form("steal_form", clear_on_submit=False):
        col_q, col_btn = st.columns([6, 1])
        with col_q:
            query = st.text_input(
                "Query",
                placeholder="e.g. stripe webhook, ActiveRecord, parse_price…",
                label_visibility="collapsed",
            )
        with col_btn:
            submitted = st.form_submit_button(
                "Steal",
                icon=":material/content_copy:",
                width="stretch",
            )
        opt_cols = st.columns([2, 2])
        with opt_cols[0]:
            language = st.text_input(
                "Language filter (ext)",
                placeholder="py, rb, ts, go…",
            )
        with opt_cols[1]:
            limit = st.number_input(
                "Max blocks",
                min_value=1,
                max_value=20,
                value=5,
                step=1,
            )

    if submitted:
        cleaned = query.strip()
        if not cleaned:
            st.session_state.pop(_STATE_KEY, None)
            return
        lang = language.strip() or None
        try:
            hits = steal(cleaned, limit=int(limit), language=lang)
        except RuntimeError as exc:
            st.error(str(exc))
            return
        st.session_state[_STATE_KEY] = StealResults(
            query=cleaned,
            language=lang,
            limit=int(limit),
            hits=hits,
        )


def _render_results() -> None:
    """Render the persisted StealResults, if any."""
    state = st.session_state.get(_STATE_KEY)
    if not isinstance(state, StealResults):
        return

    if not state.hits:
        st.warning(
            f"No reusable blocks for '{state.query}'. "
            "Run **Scan now** in the sidebar, or try a broader query."
        )
        return

    st.success(
        f"{len(state.hits)} block(s) for '{state.query}'"
        + (f" (lang={state.language})" if state.language else "")
    )

    for idx, r in enumerate(state.hits, start=1):
        _render_result_card(idx, r, state.query)


def _render_result_card(idx: int, r: StealResult, query: str) -> None:
    """One result card: header + code + copy/vote buttons."""
    status = r.project_status or "unknown"
    header = f"**{idx}. {r.project_name}**  ·  `{status}`  ·  score `{r.score:.2f}`"
    st.markdown(header)

    symbol_part = f"`{r.block.symbol}`  " if r.block.symbol else ""
    st.caption(f"{symbol_part}{r.block.path}:{r.block.start_line}-{r.block.end_line}")

    language = r.block.language_ext or "text"
    st.code(r.block.content, language=language, line_numbers=False)

    btn_cols = st.columns([1, 1, 1, 6])
    with btn_cols[0]:
        if st.button(
            "Copy",
            icon=":material/content_copy:",
            key=f"steal_copy_{idx}",
        ):
            _handle_copy(r, query)
    with btn_cols[1]:
        if st.button(
            "👍",
            key=f"steal_up_{idx}",
            help="This block was useful.",
        ):
            record_vote(query, r.block.path, r.block.start_line, 1)
            st.toast("Thanks — logged thumbs up.", icon="👍")
    with btn_cols[2]:
        if st.button(
            "👎",
            key=f"steal_down_{idx}",
            help="This block wasn't what I wanted.",
        ):
            record_vote(query, r.block.path, r.block.start_line, -1)
            st.toast("Noted — logged thumbs down.", icon="👎")


def _handle_copy(r: StealResult, query: str) -> None:
    """Copy block content to clipboard and journal the take."""
    content_bytes = r.block.content.encode("utf-8")
    try:
        subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["pbcopy"],
            input=content_bytes,
            check=True,
            timeout=5,
        )
        st.toast(f"Copied from {r.project_name}.", icon="📋")
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        st.warning(
            "`pbcopy` unavailable — select the block above and copy it manually."
        )
        return

    import contextlib as _ctx

    with _ctx.suppress(Exception):
        record_steal(
            query=query,
            src_path=r.block.path,
            dst_path=str(Path.cwd()),
        )

"""Thumbs up/down on Steal results (ADR 0027).

Motivation (Arvid Kahl's panel note): heuristic ranking rots silently
without a feedback signal. Votes are cheap to collect and will seed
a future ML ranker once we have ~30 per query-class.

Storage piggybacks on ``code_index.db``: we add a small non-FTS
table ``steal_feedback`` on the first vote. The table is not part of
the Steal index schema version — it is append-only and survives
re-indexing.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time

from .code_index import CodeIndex

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS steal_feedback (
    id            INTEGER PRIMARY KEY,
    query_hash    TEXT NOT NULL,
    query         TEXT NOT NULL,
    path          TEXT NOT NULL,
    start_line    INTEGER NOT NULL,
    vote          INTEGER NOT NULL,
    voted_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steal_feedback_hash
    ON steal_feedback(query_hash);

-- ADR 0031: continuous precision signal. A row per query that came
-- back empty so we can spot profiles over-pruning before users
-- complain. `path` and `start_line` mirror `steal_feedback`'s shape
-- but are unused for no-result events (kept NULL-equivalent: empty
-- string and 0) — schema parity buys nothing here, a sibling table
-- buys clarity.
CREATE TABLE IF NOT EXISTS steal_no_results (
    id            INTEGER PRIMARY KEY,
    query_hash    TEXT NOT NULL,
    query         TEXT NOT NULL,
    seen_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steal_no_results_hash
    ON steal_no_results(query_hash);
"""


def record_vote(
    query: str,
    path: str,
    start_line: int,
    vote: int,
) -> None:
    """Store a +1 or -1 vote for a specific block.

    ``vote`` is clamped to {-1, +1}; anything else is dropped.
    """
    if vote not in (-1, 1):
        return
    normalised = query.strip()
    if not normalised:
        return
    query_hash = _hash_query(normalised)
    with CodeIndex() as idx:
        _ensure_feedback_table(idx.conn)
        idx.conn.execute(
            "INSERT INTO steal_feedback "
            "(query_hash, query, path, start_line, vote, voted_at) "
            "VALUES (?,?,?,?,?,?)",
            (query_hash, normalised, path, start_line, vote, time.time()),
        )
        idx.conn.commit()


def vote_counts(query: str) -> dict[str, int]:
    """Return ``{"up": n, "down": n}`` for a normalised query string."""
    normalised = query.strip()
    if not normalised:
        return {"up": 0, "down": 0}
    query_hash = _hash_query(normalised)
    with CodeIndex() as idx:
        _ensure_feedback_table(idx.conn)
        rows = idx.conn.execute(
            "SELECT vote, COUNT(*) FROM steal_feedback "
            "WHERE query_hash = ? GROUP BY vote",
            (query_hash,),
        ).fetchall()
    out = {"up": 0, "down": 0}
    for vote, count in rows:
        if vote == 1:
            out["up"] = count
        elif vote == -1:
            out["down"] = count
    return out


def record_no_results(query: str) -> None:
    """Log that ``query`` returned no Steal hits (ADR 0031 signal).

    Cheap append. Aggregated by :func:`no_results_count` so we can
    detect profiles over-pruning a query class — Arvid's continuous
    feedback loop instead of a one-shot precision benchmark.
    """
    normalised = query.strip()
    if not normalised:
        return
    query_hash = _hash_query(normalised)
    with CodeIndex() as idx:
        _ensure_feedback_table(idx.conn)
        idx.conn.execute(
            "INSERT INTO steal_no_results (query_hash, query, seen_at) VALUES (?,?,?)",
            (query_hash, normalised, time.time()),
        )
        idx.conn.commit()


def no_results_count(query: str) -> int:
    """Return how many times ``query`` has been logged as zero-hit."""
    normalised = query.strip()
    if not normalised:
        return 0
    query_hash = _hash_query(normalised)
    with CodeIndex() as idx:
        _ensure_feedback_table(idx.conn)
        row = idx.conn.execute(
            "SELECT COUNT(*) FROM steal_no_results WHERE query_hash = ?",
            (query_hash,),
        ).fetchone()
    return int(row[0]) if row else 0


def _hash_query(query: str) -> str:
    return hashlib.sha1(query.lower().encode("utf-8")).hexdigest()  # noqa: S324


def _ensure_feedback_table(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)

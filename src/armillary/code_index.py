"""SQLite FTS5 index for cross-repo code blocks (ADR 0027 — Steal).

Separate database file from the project cache (cache.db). Steal's
index shape evolves on its own cadence, so reusing cache.py's
user_version would force a project-cache rebuild on every Steal
schema tweak. Isolation wins.

Schema version is bumped in this module; on mismatch both tables
are dropped and rebuilt — the next scan repopulates.

FTS5 is required. If the current Python's sqlite3 wasn't built with
FTS5, `open()` raises with a clear message.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import NamedTuple, Self

from .cache import default_db_path as _default_project_db_path

SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE code_blocks_meta (
    id           INTEGER PRIMARY KEY,
    repo_path    TEXT NOT NULL,
    path         TEXT NOT NULL,
    language_ext TEXT NOT NULL,
    start_line   INTEGER NOT NULL,
    end_line     INTEGER NOT NULL,
    symbol       TEXT,
    updated_at   REAL NOT NULL
);
CREATE INDEX idx_code_blocks_repo_path ON code_blocks_meta(repo_path, path);
CREATE INDEX idx_code_blocks_language  ON code_blocks_meta(language_ext);
CREATE INDEX idx_code_blocks_updated   ON code_blocks_meta(updated_at);

CREATE VIRTUAL TABLE code_blocks_fts USING fts5(
    content
);
"""


def default_index_path() -> Path:
    """Location of the code-index SQLite file.

    Follows the same platform conventions as the project cache, lives
    in the same directory. Override via ``ARMILLARY_CODE_INDEX_DB``.
    """
    override = os.environ.get("ARMILLARY_CODE_INDEX_DB")
    if override:
        return Path(override).expanduser()
    return _default_project_db_path().parent / "code_index.db"


class CodeBlockRow(NamedTuple):
    """A single indexed code block, as returned by ``CodeIndex.search``."""

    repo_path: str
    path: str
    language_ext: str
    start_line: int
    end_line: int
    content: str
    symbol: str | None
    updated_at: float


class CodeIndex:
    """Context-managed SQLite + FTS5 index for code blocks.

    Usage:

        with CodeIndex() as idx:
            idx.upsert_blocks(repo_path, path, blocks)
            rows = idx.search("stripe webhook", limit=5)
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or default_index_path()
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        _require_fts5(self._conn)
        self._ensure_schema()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError(
                "CodeIndex is not open. Use `with CodeIndex() as idx:` or call open()."
            )
        return self._conn

    def _ensure_schema(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            existing = self.conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table','virtual') AND name='code_blocks_meta'"
            ).fetchone()
            if existing is not None:
                return
        # Wrong version or missing tables — wipe and recreate.
        self.conn.execute("DROP TABLE IF EXISTS code_blocks_fts")
        self.conn.execute("DROP TABLE IF EXISTS code_blocks_meta")
        self.conn.executescript(_SCHEMA_SQL)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    # ----- mutations --------------------------------------------------------

    def upsert_blocks(
        self,
        repo_path: str,
        path: str,
        blocks: Iterable[object],
    ) -> int:
        """Replace all blocks for a single file atomically.

        ``blocks`` is an iterable of ``code_block_service.CodeBlock``
        instances (duck-typed to avoid a circular import at module
        load). Returns the number of rows inserted.
        """
        blocks_list = list(blocks)
        with self.conn:
            # Delete existing FTS rows matching this file via the meta ids.
            old_ids = [
                row[0]
                for row in self.conn.execute(
                    "SELECT id FROM code_blocks_meta WHERE repo_path = ? AND path = ?",
                    (repo_path, path),
                )
            ]
            if old_ids:
                qmarks = ",".join("?" * len(old_ids))
                self.conn.execute(
                    f"DELETE FROM code_blocks_fts WHERE rowid IN ({qmarks})",
                    old_ids,
                )
                self.conn.execute(
                    f"DELETE FROM code_blocks_meta WHERE id IN ({qmarks})",
                    old_ids,
                )
            for blk in blocks_list:
                cur = self.conn.execute(
                    "INSERT INTO code_blocks_meta "
                    "(repo_path, path, language_ext, start_line, end_line, "
                    " symbol, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        blk.repo_path,
                        blk.path,
                        blk.language_ext,
                        blk.start_line,
                        blk.end_line,
                        blk.symbol,
                        blk.updated_at,
                    ),
                )
                self.conn.execute(
                    "INSERT INTO code_blocks_fts (rowid, content) VALUES (?,?)",
                    (cur.lastrowid, blk.content),
                )
        return len(blocks_list)

    def delete_repo(self, repo_path: str) -> int:
        """Remove every block belonging to ``repo_path``."""
        with self.conn:
            old_ids = [
                row[0]
                for row in self.conn.execute(
                    "SELECT id FROM code_blocks_meta WHERE repo_path = ?",
                    (repo_path,),
                )
            ]
            if not old_ids:
                return 0
            qmarks = ",".join("?" * len(old_ids))
            self.conn.execute(
                f"DELETE FROM code_blocks_fts WHERE rowid IN ({qmarks})",
                old_ids,
            )
            self.conn.execute(
                f"DELETE FROM code_blocks_meta WHERE id IN ({qmarks})",
                old_ids,
            )
        return len(old_ids)

    # ----- reads ------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int,
        language_ext: str | None = None,
    ) -> list[CodeBlockRow]:
        """FTS5 MATCH, ordered by BM25 relevance.

        Without an explicit ``ORDER BY``, FTS5 returns rows in rowid
        order — which is "insertion order", i.e. whichever repo was
        indexed first saturates the overfetch window. BM25 makes the
        overfetch *relevance-representative* so ``steal_service`` can
        re-rank the top candidates by recency and project status.
        Empty/whitespace query returns []; malformed FTS syntax is
        swallowed to an empty result.
        """
        if not query.strip():
            return []
        safe_query = _sanitize_fts_query(query)
        if not safe_query:
            return []
        sql_parts = [
            "SELECT m.repo_path, m.path, m.language_ext, m.start_line, "
            "m.end_line, f.content, m.symbol, m.updated_at "
            "FROM code_blocks_fts f "
            "JOIN code_blocks_meta m ON m.id = f.rowid "
            "WHERE code_blocks_fts MATCH ?"
        ]
        params: list[object] = [safe_query]
        if language_ext is not None:
            sql_parts.append("AND m.language_ext = ?")
            params.append(language_ext)
        # bm25() returns smaller = better match. ORDER BY ascending.
        sql_parts.append("ORDER BY bm25(code_blocks_fts)")
        sql_parts.append("LIMIT ?")
        params.append(max(1, limit))
        try:
            rows = self.conn.execute(" ".join(sql_parts), params).fetchall()
        except sqlite3.OperationalError:
            # Malformed FTS expression despite sanitisation.
            return []
        return [
            CodeBlockRow(
                repo_path=row[0],
                path=row[1],
                language_ext=row[2],
                start_line=row[3],
                end_line=row[4],
                content=row[5],
                symbol=row[6],
                updated_at=row[7],
            )
            for row in rows
        ]


# ----- helpers --------------------------------------------------------------


def _require_fts5(conn: sqlite3.Connection) -> None:
    """Raise RuntimeError if sqlite3 was compiled without FTS5."""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts5_probe")
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            "armillary steal requires SQLite FTS5. This Python's sqlite3 "
            "module was built without it. Install a Python with FTS5 "
            "support (pyenv, brew, etc.)."
        ) from exc


_FTS_SPECIAL = set('"*:()[]^+-')


def _sanitize_fts_query(query: str) -> str:
    """Produce a safe FTS5 MATCH expression from user input.

    Each whitespace-delimited token becomes a prefix-matched quoted
    phrase (``"token" *``). Prefix matching widens the recall: a
    query like ``linked_flow`` now matches ``linked_flow_policy``,
    ``linked_flow_service``, etc., which FTS5's simple tokenizer
    would otherwise treat as distinct tokens.

    CamelCase vs snake_case is a separate concern — FTS5's simple
    tokenizer does not split ``LinkedFlow`` into ``linked`` + ``flow``,
    so users searching for ``linked_flow`` will not hit ``LinkedFlow``.
    Callers who care should submit both variants or use the dedicated
    matcher (future work).

    Colons and other FTS5 operators are stripped from tokens to avoid
    injecting column-scoped / boolean syntax.
    """
    tokens: list[str] = []
    for raw in query.split():
        cleaned = "".join("_" if ch in _FTS_SPECIAL else ch for ch in raw)
        cleaned = cleaned.strip("_")
        if cleaned:
            # Prefix form: ``token*`` (no quotes — quotes disable prefix).
            # ``cleaned`` has already had FTS operators replaced, so it is
            # safe to embed directly.
            tokens.append(f"{cleaned}*")
    return " ".join(tokens)

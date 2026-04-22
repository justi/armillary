"""Tests for code_index + steal_service — FTS5 index + ranked retrieval."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from armillary.code_block_service import CodeBlock
from armillary.code_index import CodeBlockRow, CodeIndex


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ARMILLARY_CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.setenv("ARMILLARY_CODE_INDEX_DB", str(tmp_path / "code_index.db"))
    return tmp_path


def _make_block(
    *,
    repo: str = "/repo/a",
    path: str = "/repo/a/mod.py",
    content: str = "def foo():\n    return 1",
    symbol: str | None = "foo",
    start: int = 1,
    end: int = 2,
    ext: str = "py",
    updated_at: float | None = None,
) -> CodeBlock:
    return CodeBlock(
        repo_path=repo,
        path=path,
        language_ext=ext,
        start_line=start,
        end_line=end,
        content=content,
        symbol=symbol,
        updated_at=updated_at or time.time(),
    )


def test_empty_query_returns_empty(isolated_cache: Path) -> None:
    with CodeIndex() as idx:
        idx.upsert_blocks("/repo/a", "/repo/a/f.py", [_make_block()])
        assert idx.search("", limit=5) == []


def test_fts_search_returns_rows(isolated_cache: Path) -> None:
    blk = _make_block(content="stripe webhook handler for payments")
    with CodeIndex() as idx:
        idx.upsert_blocks(blk.repo_path, blk.path, [blk])
        rows = idx.search("stripe", limit=5)
        assert len(rows) == 1
        assert isinstance(rows[0], CodeBlockRow)
        assert "stripe" in rows[0].content


def test_language_filter(isolated_cache: Path) -> None:
    py_blk = _make_block(path="/repo/a/x.py", content="needle in py", ext="py")
    rb_blk = _make_block(
        path="/repo/a/x.rb", content="needle in rb", ext="rb", symbol="Foo"
    )
    with CodeIndex() as idx:
        idx.upsert_blocks(py_blk.repo_path, py_blk.path, [py_blk])
        idx.upsert_blocks(rb_blk.repo_path, rb_blk.path, [rb_blk])
        rows = idx.search("needle", limit=10, language_ext="rb")
        assert len(rows) == 1
        assert rows[0].language_ext == "rb"


def test_upsert_replaces_existing_rows_for_same_file(isolated_cache: Path) -> None:
    old = _make_block(content="old needle here")
    new = _make_block(content="new needle here")
    with CodeIndex() as idx:
        idx.upsert_blocks(old.repo_path, old.path, [old])
        idx.upsert_blocks(new.repo_path, new.path, [new])
        rows = idx.search("needle", limit=10)
        assert len(rows) == 1
        assert "new" in rows[0].content


def test_delete_repo_removes_all_blocks(isolated_cache: Path) -> None:
    b1 = _make_block(path="/repo/a/one.py", content="needle one")
    b2 = _make_block(path="/repo/a/two.py", content="needle two")
    with CodeIndex() as idx:
        idx.upsert_blocks(b1.repo_path, b1.path, [b1])
        idx.upsert_blocks(b2.repo_path, b2.path, [b2])
        assert len(idx.search("needle", limit=10)) == 2
        idx.delete_repo(b1.repo_path)
        assert idx.search("needle", limit=10) == []


def test_special_fts_characters_are_sanitised(isolated_cache: Path) -> None:
    blk = _make_block(content="stripe_webhook handler")
    with CodeIndex() as idx:
        idx.upsert_blocks(blk.repo_path, blk.path, [blk])
        # Quotes and colons would normally blow up an FTS MATCH.
        rows = idx.search('stripe"webhook:', limit=5)
        # Sanitisation may turn this into a single-token match; at
        # minimum it must not raise.
        assert isinstance(rows, list)


def test_steal_empty_query(isolated_cache: Path) -> None:
    from armillary.steal_service import steal

    assert steal("") == []
    assert steal("   ") == []


def test_steal_bm25_relevance_dominates(isolated_cache: Path) -> None:
    """Dense BM25 match in an old file beats a weak match in a fresh one.

    Ranking mistake we want to avoid: actively-edited but off-topic code
    saturated the top of the results because recency alone was the
    dominant signal. BM25 rank position corrects this.
    """
    from armillary.steal_service import steal

    now = time.time()
    weak_fresh = _make_block(
        path="/repo/a/fresh.py",
        content="the word needle appears here once, surrounded by filler",
        updated_at=now - 60,
    )
    dense_old = _make_block(
        path="/repo/a/old.py",
        content="needle needle needle needle needle",
        updated_at=now - 86400 * 365,
    )
    with CodeIndex() as idx:
        idx.upsert_blocks(weak_fresh.repo_path, weak_fresh.path, [weak_fresh])
        idx.upsert_blocks(dense_old.repo_path, dense_old.path, [dense_old])

    results = steal("needle", limit=5)
    assert len(results) == 2
    assert results[0].block.path.endswith("old.py")
    assert results[1].block.path.endswith("fresh.py")


def test_steal_language_filter(isolated_cache: Path) -> None:
    from armillary.steal_service import steal

    py = _make_block(path="/r/a.py", content="needle A", ext="py")
    rb = _make_block(path="/r/b.rb", content="needle B", ext="rb")
    with CodeIndex() as idx:
        idx.upsert_blocks(py.repo_path, py.path, [py])
        idx.upsert_blocks(rb.repo_path, rb.path, [rb])

    results = steal("needle", limit=5, language="py")
    assert len(results) == 1
    assert results[0].block.language_ext == "py"


def test_code_outranks_docs_and_data(isolated_cache: Path) -> None:
    """Same age + same (empty) project status → code file wins over .md and .json."""
    from armillary.steal_service import steal

    now = time.time()
    doc = _make_block(
        path="/r/a/readme.md",
        content="stripe webhook docs",
        ext="md",
        updated_at=now,
    )
    data = _make_block(
        path="/r/a/fixtures.json",
        content="stripe webhook fixture",
        ext="json",
        updated_at=now,
    )
    code = _make_block(
        path="/r/a/handler.py",
        content="stripe webhook handler",
        ext="py",
        updated_at=now,
    )
    with CodeIndex() as idx:
        idx.upsert_blocks(doc.repo_path, doc.path, [doc])
        idx.upsert_blocks(data.repo_path, data.path, [data])
        idx.upsert_blocks(code.repo_path, code.path, [code])

    results = steal("stripe webhook", limit=5)
    assert len(results) == 3
    # Ordering: code (1.0) > doc (0.55) > data (0.4)
    exts = [r.block.language_ext for r in results]
    assert exts == ["py", "md", "json"]

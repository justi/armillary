"""Tests for code_block_service — window extraction + symbol heuristic."""

from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from armillary.code_block_service import (
    CodeBlock,
    _extract_symbol,
    _should_skip,
    build_blocks_for_repo,
    build_blocks_with_stats,
)
from armillary.framework_profiles import Profile


def _init_git_repo(root: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", "init", "-q", str(root)], check=True
    )
    subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "config", "user.email", "t@example.com"],
        check=True,
    )
    subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "config", "user.name", "t"], check=True
    )


def _git_add_commit(root: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "add", "-A"], check=True
    )
    subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True
    )


def test_non_git_directory_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "alpha.py").write_text("def x():\n    pass\n")
    assert build_blocks_for_repo(tmp_path) == []


def test_git_repo_yields_blocks_with_language_ext(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "alpha.py").write_text("def needle():\n    return 1\n")
    (tmp_path / "beta.rb").write_text("class Account\n  def call\n    42\n  end\nend\n")
    _git_add_commit(tmp_path)

    blocks = build_blocks_for_repo(tmp_path)
    exts = {b.language_ext for b in blocks}
    assert "py" in exts
    assert "rb" in exts
    # symbol captured for each
    symbols = {b.symbol for b in blocks if b.symbol}
    assert "needle" in symbols
    assert "Account" in symbols


def test_size_cap_skips_large_files(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    big = "x" * (600 * 1024)
    (tmp_path / "big.txt").write_text(big)
    (tmp_path / "ok.py").write_text("def ok():\n    pass\n")
    _git_add_commit(tmp_path)

    blocks = build_blocks_for_repo(tmp_path)
    paths = {Path(b.path).name for b in blocks}
    assert "big.txt" not in paths
    assert "ok.py" in paths


def test_binary_files_skipped(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "blob.bin").write_bytes(b"some\x00binary\x00data\n" * 20)
    (tmp_path / "x.py").write_text("def x():\n    pass\n")
    _git_add_commit(tmp_path)

    blocks = build_blocks_for_repo(tmp_path)
    names = {Path(b.path).name for b in blocks}
    assert "blob.bin" not in names
    assert "x.py" in names


def test_skip_vendor_dirs(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    vendor = tmp_path / "node_modules" / "pkg"
    vendor.mkdir(parents=True)
    (vendor / "idx.js").write_text("function foo() { return 1 }\n")
    (tmp_path / "main.js").write_text("function main() { return 2 }\n")
    _git_add_commit(tmp_path)

    blocks = build_blocks_for_repo(tmp_path)
    paths = {Path(b.path).name for b in blocks}
    assert "idx.js" not in paths
    assert "main.js" in paths


def test_should_skip_double_suffix() -> None:
    assert _should_skip("dist/app.min.js", Path("/tmp/app.min.js"))
    assert _should_skip("bundle.min.css", Path("/tmp/bundle.min.css"))


def test_symbol_heuristic_patterns() -> None:
    assert _extract_symbol(["def my_fn(x):"]) == "my_fn"
    assert _extract_symbol(["async def fetch():"]) == "fetch"
    assert _extract_symbol(["class Foo:"]) == "Foo"
    assert _extract_symbol(["function bar() {"]) == "bar"
    assert _extract_symbol(["export function baz() {"]) == "baz"
    assert _extract_symbol(["export default class Widget {"]) == "Widget"
    assert _extract_symbol(["func qux() {"]) == "qux"
    assert _extract_symbol(["fn fox() {"]) == "fox"
    # non-declaration line → None
    assert _extract_symbol(["# just a comment"]) is None
    assert _extract_symbol(["return 1"]) is None
    # blank lines skipped, first non-blank inspected
    assert _extract_symbol(["", "", "def foo():"]) == "foo"


def test_sliding_window_overlaps(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    content = "\n".join(f"line {i}" for i in range(100)) + "\n"
    (tmp_path / "long.py").write_text(content)
    _git_add_commit(tmp_path)
    blocks = build_blocks_for_repo(tmp_path)

    # windows of 40 with stride 20 over 100 lines → starts at 1,21,41,61,
    # then ≥ total → break. Last window should end at line 100.
    starts = sorted({b.start_line for b in blocks})
    assert starts[0] == 1
    assert 21 in starts
    assert 41 in starts
    # last end_line is clamped to 100
    assert max(b.end_line for b in blocks) == 100


def test_small_file_single_window(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "tiny.py").write_text("def a():\n    return 1\n")
    _git_add_commit(tmp_path)

    blocks = [
        b for b in build_blocks_for_repo(tmp_path) if Path(b.path).name == "tiny.py"
    ]
    assert len(blocks) == 1
    assert blocks[0].start_line == 1
    assert blocks[0].end_line == 2


@pytest.fixture
def _isolated_git_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep git subprocesses from picking up user config / commit signing.
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")


def test_codeblock_is_frozen() -> None:
    blk = CodeBlock(
        repo_path="/r",
        path="/r/a.py",
        language_ext="py",
        start_line=1,
        end_line=2,
        content="x",
        symbol=None,
        updated_at=0.0,
    )
    with pytest.raises((AttributeError, TypeError, FrozenInstanceError)):
        blk.path = "/r/b.py"  # type: ignore[misc]


# ----- ADR 0031 — framework profile filtering ------------------------------


def _rails_layout_repo(root: Path) -> None:
    """Set up a tiny git repo that mimics a Rails skeleton."""
    _init_git_repo(root)
    (root / "Gemfile").write_text('gem "rails"\n')
    (root / "app" / "models").mkdir(parents=True)
    (root / "app" / "models" / "user.rb").write_text(
        "class User\n  def login\n    :ok\n  end\nend\n"
    )
    (root / "db" / "migrate").mkdir(parents=True)
    (root / "db" / "migrate" / "20240101_create_users.rb").write_text(
        "class CreateUsers < ActiveRecord::Migration[7.0]\n  def change\n  end\nend\n"
    )
    (root / "config").mkdir()
    (root / "config" / "routes.rb").write_text(
        "Rails.application.routes.draw do\nend\n"
    )
    _git_add_commit(root)


def test_profile_drops_files_outside_include(tmp_path: Path) -> None:
    _rails_layout_repo(tmp_path)
    rails = Profile(name="rails", include=("app/", "lib/"))

    blocks = build_blocks_for_repo(tmp_path, profile=rails)

    rels = {Path(b.path).relative_to(tmp_path).as_posix() for b in blocks}
    assert "app/models/user.rb" in rels
    # db/migrate and config are filtered out by the profile.
    assert not any(r.startswith("db/migrate/") for r in rels)
    assert not any(r.startswith("config/") for r in rels)


def test_profile_none_keeps_legacy_index_everything_behaviour(tmp_path: Path) -> None:
    _rails_layout_repo(tmp_path)
    blocks = build_blocks_for_repo(tmp_path, profile=None)
    rels = {Path(b.path).relative_to(tmp_path).as_posix() for b in blocks}
    # No profile = same as ADR 0027 v1 — db/migrate IS indexed.
    assert any(r.startswith("db/migrate/") for r in rels)


def test_profile_stats_record_indexed_and_skipped_counts(tmp_path: Path) -> None:
    _rails_layout_repo(tmp_path)
    rails = Profile(name="rails", include=("app/",))
    _, result = build_blocks_with_stats(tmp_path, profile=rails)
    assert result.profile_name == "rails"
    assert result.files_indexed >= 1  # at least app/models/user.rb
    assert result.files_skipped >= 2  # Gemfile, db/migrate, config files


def test_profile_indexes_zero_files_observable(tmp_path: Path) -> None:
    """Custom Rails layout with no app/ — observability regression guard."""
    _init_git_repo(tmp_path)
    (tmp_path / "Gemfile").write_text('gem "rails"\n')
    # No app/ — engine-style or unusual layout.
    (tmp_path / "engines").mkdir()
    (tmp_path / "engines" / "billing.rb").write_text("class Billing\nend\n")
    _git_add_commit(tmp_path)

    rails = Profile(name="rails", include=("app/", "lib/"))
    blocks, result = build_blocks_with_stats(tmp_path, profile=rails)
    assert blocks == []
    assert result.files_indexed == 0
    # The Gemfile + engines/billing.rb were filtered → at least 2 skipped.
    assert result.files_skipped >= 2

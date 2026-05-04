"""Tests for ``armillary.revive_enhanced`` — composes vanilla revive with
top-N matching code blocks from other repositories.

The unit covers helper logic only; the MCP-side wrapper
(``armillary_revive``) lives in ``test_mcp_server.py``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from armillary.code_index import CodeBlockRow
from armillary.revive_enhanced import generate_enhanced_brief
from armillary.revive_service import ReviveError
from armillary.steal_service import StealResult


def _block(
    *,
    repo_path: str = "/repos/other",
    path: str = "src/foo.py",
    language_ext: str = "py",
    start_line: int = 10,
    end_line: int = 49,
    content: str = "def foo():\n    return 1\n",
    symbol: str | None = "foo",
) -> CodeBlockRow:
    return CodeBlockRow(
        repo_path=repo_path,
        path=path,
        language_ext=language_ext,
        start_line=start_line,
        end_line=end_line,
        content=content,
        symbol=symbol,
        updated_at=0.0,
    )


def _result(
    block: CodeBlockRow,
    *,
    project_name: str = "other",
    project_status: str | None = "ACTIVE",
    score: float = 1.0,
) -> StealResult:
    return StealResult(
        block=block,
        project_name=project_name,
        project_status=project_status,
        score=score,
    )


def _patch_helper_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    revive_output: str = "BRIEF",
    steal_results: list[StealResult] | None = None,
    cache_project_name: str | None = None,
    revive_raises: ReviveError | None = None,
) -> dict[str, MagicMock]:
    """Replace external collaborators on the revive_enhanced module."""
    revive_mock = MagicMock(return_value=revive_output)
    if revive_raises is not None:
        revive_mock.side_effect = revive_raises
    monkeypatch.setattr("armillary.revive_enhanced.revive_show", revive_mock)

    steal_mock = MagicMock(return_value=steal_results or [])
    monkeypatch.setattr("armillary.revive_enhanced.steal", steal_mock)

    cache_mock = MagicMock()
    cached_project = MagicMock(name=cache_project_name) if cache_project_name else None
    if cached_project is not None:
        cached_project.name = cache_project_name
    cache_mock.return_value.__enter__.return_value.get_project.return_value = (
        cached_project
    )
    cache_mock.return_value.__exit__.return_value = False
    monkeypatch.setattr("armillary.revive_enhanced.Cache", cache_mock)

    return {
        "revive": revive_mock,
        "steal": steal_mock,
        "cache": cache_mock,
    }


def test_generate_enhanced_brief_appends_steal_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block = _block(symbol="parse_price", path="src/price.py")
    mocks = _patch_helper_dependencies(
        monkeypatch,
        revive_output="# project brief\n",
        steal_results=[_result(block, project_name="invoicer")],
        cache_project_name="my_project",
    )
    out = generate_enhanced_brief(Path("/repos/my_project"))

    assert out.startswith("# project brief\n")
    assert "## STEAL_HITS — code you wrote in other repos" in out
    assert "- invoicer/src/price.py:10-49 — parse_price" in out
    assert "```py\ndef foo():\n    return 1\n\n```" in out
    # KISS query strategy: project name only — empirically the only
    # signal that yields cross-repo matches without precision-extreme
    # AND-collapse on commit subjects.
    assert mocks["steal"].call_args.args[0] == "my_project"
    # Helper overfetches (limit * 6) so it can drop hits from the same
    # repo and from panel-excluded / archived repos before slicing back
    # to the requested top-N. The 6× multiplier is sized for the worst
    # case where three noise repos consume 9 results (steal caps at 3
    # hits per repo in its overfetch pool).
    assert mocks["steal"].call_args.kwargs == {"limit": 18}


def test_generate_enhanced_brief_skips_section_when_no_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_helper_dependencies(
        monkeypatch,
        revive_output="vanilla\n",
        steal_results=[],
        cache_project_name="alpha",
    )
    out = generate_enhanced_brief(Path("/repos/alpha"))

    assert out == "vanilla\n"
    assert "STEAL_HITS" not in out


def test_generate_enhanced_brief_propagates_revive_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_helper_dependencies(
        monkeypatch,
        revive_raises=ReviveError("revive binary not found on PATH"),
    )

    with pytest.raises(ReviveError, match="not found"):
        generate_enhanced_brief(Path("/repos/x"))


def test_generate_enhanced_brief_uses_path_name_when_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mocks = _patch_helper_dependencies(
        monkeypatch,
        revive_output="b\n",
        steal_results=[_result(_block())],
        cache_project_name=None,
    )
    generate_enhanced_brief(Path("/repos/uncached_dir"))

    # Falls back to path.name when cache returns None.
    assert mocks["steal"].call_args.args[0] == "uncached_dir"


def test_generate_enhanced_brief_caps_at_steal_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extras = [
        _result(_block(symbol=f"sym{i}", path=f"src/{i}.py"), project_name=f"p{i}")
        for i in range(5)
    ]
    _patch_helper_dependencies(
        monkeypatch,
        steal_results=extras,
        cache_project_name="me",
    )
    out = generate_enhanced_brief(Path("/repos/me"))

    # Only the first three should appear in the rendered output.
    assert out.count("- p") == 3
    assert "p0/src/0.py" in out
    assert "p1/src/1.py" in out
    assert "p2/src/2.py" in out
    assert "p3/src/3.py" not in out


def test_generate_enhanced_brief_uses_no_symbol_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_helper_dependencies(
        monkeypatch,
        steal_results=[_result(_block(symbol=None), project_name="anon")],
        cache_project_name="me",
    )
    out = generate_enhanced_brief(Path("/repos/me"))

    assert "— (no symbol)" in out


def test_generate_enhanced_brief_falls_back_when_steal_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the code index is corrupt or built without FTS5, ``steal()``
    can raise. The vanilla brief is still useful by itself, so the
    helper degrades gracefully (Copilot review on PR #37).
    """
    mocks = _patch_helper_dependencies(
        monkeypatch,
        revive_output="# vanilla brief\n",
        cache_project_name="me",
    )
    mocks["steal"].side_effect = RuntimeError("code_index.db missing")

    out = generate_enhanced_brief(Path("/repos/me"))

    assert out == "# vanilla brief\n"
    assert "STEAL_HITS" not in out


def test_generate_enhanced_brief_renders_relative_block_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``CodeBlockRow.path`` is absolute in production. The display
    bullet must strip the ``repo_path`` prefix so the line reads
    ``project/src/x.py`` instead of leaking the absolute path.
    """
    abs_block = _block(
        repo_path="/repos/invoicer",
        path="/repos/invoicer/src/price.py",
        symbol="parse_price",
    )
    _patch_helper_dependencies(
        monkeypatch,
        steal_results=[_result(abs_block, project_name="invoicer")],
        cache_project_name="me",
    )
    out = generate_enhanced_brief(Path("/repos/me"))

    assert "invoicer/src/price.py:" in out
    # Absolute path must NOT appear (would mean we skipped the strip).
    assert "/repos/invoicer/src/price.py" not in out


def test_generate_enhanced_brief_drops_excluded_repos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repos the user has excluded via the panel must not appear in
    STEAL_HITS, even though `steal()` itself returns them. Mirrors how
    `armillary_projects` and `armillary_next` already behave.
    """
    excluded_block = _block(
        repo_path="/repos/dead-fork", path="/repos/dead-fork/src/x.py"
    )
    keep_block = _block(
        repo_path="/repos/active-sibling", path="/repos/active-sibling/src/y.py"
    )
    _patch_helper_dependencies(
        monkeypatch,
        steal_results=[
            _result(excluded_block, project_name="dead-fork"),
            _result(keep_block, project_name="active-sibling"),
        ],
        cache_project_name="me",
    )

    def _is_excluded(path: str) -> bool:
        return path == "/repos/dead-fork"

    monkeypatch.setattr("armillary.revive_enhanced.is_excluded", _is_excluded)

    out = generate_enhanced_brief(Path("/repos/me"))

    assert "active-sibling/src/y.py" in out
    assert "dead-fork" not in out


def test_generate_enhanced_brief_drops_archived_repos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repos the user archived via status override must also be dropped."""
    from armillary.models import Status

    archived_block = _block(repo_path="/repos/old", path="/repos/old/src/x.py")
    keep_block = _block(repo_path="/repos/active", path="/repos/active/src/y.py")
    _patch_helper_dependencies(
        monkeypatch,
        steal_results=[
            _result(archived_block, project_name="old"),
            _result(keep_block, project_name="active"),
        ],
        cache_project_name="me",
    )

    def _override(path: str) -> Status | None:
        return Status.ARCHIVED if path == "/repos/old" else None

    monkeypatch.setattr("armillary.revive_enhanced.get_override", _override)

    out = generate_enhanced_brief(Path("/repos/me"))

    assert "active/src/y.py" in out
    assert "old/src/x.py" not in out


def test_generate_enhanced_brief_handles_repo_prefix_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Display-path stripping must be path-component-aware, not raw
    string startswith. ``/repos/app`` is NOT a prefix of
    ``/repos/app2/src/x.py`` even though startswith says yes
    (Copilot review on PR #37).
    """
    sibling_block = _block(
        # The current project being revived is `/repos/app`, but the
        # steal hit comes from a sibling repo `/repos/app2`.
        repo_path="/repos/app2",
        path="/repos/app2/src/x.py",
        symbol="x",
    )
    _patch_helper_dependencies(
        monkeypatch,
        steal_results=[_result(sibling_block, project_name="app2")],
        cache_project_name="app",
    )
    out = generate_enhanced_brief(Path("/repos/app"))

    # The sibling's path should be rendered relative to its OWN
    # repo_path, not chopped at "/repos/app" length.
    assert "app2/src/x.py:" in out
    # Naive startswith would have produced "2/src/x.py".
    assert "/2/src/x.py" not in out
    assert "app2/2/src/x.py" not in out


def test_generate_enhanced_brief_excludes_own_repo_from_steal_hits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The tool promises quotes from OTHER repos — must not echo a hit
    that comes from the project being revived (codex P2 finding)."""
    own_dir = tmp_path / "me"
    own_dir.mkdir()
    other_dir = tmp_path / "invoicer"
    other_dir.mkdir()

    own_block = _block(repo_path=str(own_dir), path="src/self.py", symbol="self_hit")
    other_block = _block(
        repo_path=str(other_dir), path="src/other.py", symbol="other_hit"
    )
    _patch_helper_dependencies(
        monkeypatch,
        steal_results=[
            _result(own_block, project_name="me"),  # would normally be top hit
            _result(other_block, project_name="invoicer"),
        ],
        cache_project_name="me",
    )

    out = generate_enhanced_brief(own_dir)

    assert "src/other.py" in out
    assert "src/self.py" not in out
    assert "other_hit" in out
    assert "self_hit" not in out


def test_generate_enhanced_brief_expanduser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mocks = _patch_helper_dependencies(
        monkeypatch,
        steal_results=[],
        cache_project_name="me",
    )
    generate_enhanced_brief(Path("~/foo/bar"))

    # revive_show receives the expanded path, never the literal `~`.
    called_path: Path = mocks["revive"].call_args.args[0]
    assert "~" not in str(called_path)


# --- end-to-end (real Cache + real CodeIndex) ------------------------------


def test_armillary_revive_e2e_with_real_cache_and_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exercise the full chain end-to-end via the MCP tool.

    Real Cache (temp DB), real CodeIndex (one indexed block). Only
    ``revive_show`` is patched, because the ``revive`` CLI is not
    guaranteed to exist in CI environments. No real git repo is needed
    because the v0.1 query strategy is project_name only.
    """
    from armillary.cache import Cache
    from armillary.code_block_service import CodeBlock
    from armillary.code_index import CodeIndex
    from armillary.mcp_server import armillary_revive
    from armillary.models import Project, ProjectMetadata, ProjectType, Status

    # Route both DBs to the temp dir so this test does not pollute the
    # user's real cache.
    monkeypatch.setenv("ARMILLARY_CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.setenv("ARMILLARY_CODE_INDEX_DB", str(tmp_path / "code_index.db"))

    # 1) Project to revive — name will become the steal() query.
    target_repo = tmp_path / "my_project"
    target_repo.mkdir()

    # 2) Cache row so the helper resolves the cached project name (not
    #    the path basename fallback).
    with Cache() as cache:
        cache.upsert(
            [
                Project(
                    path=target_repo,
                    name="my_project",
                    type=ProjectType.GIT,
                    umbrella=tmp_path,
                    last_modified=datetime(2026, 5, 4, 12, 0, 0),
                    metadata=ProjectMetadata(status=Status.ACTIVE),
                ),
            ]
        )

    # 3) CodeIndex with one block in a *different* repo, so steal returns it.
    other_repo = tmp_path / "invoicer"
    (other_repo / "src").mkdir(parents=True)
    other_file = other_repo / "src" / "price.py"
    other_file.write_text("placeholder\n")
    block = CodeBlock(
        repo_path=str(other_repo),
        # Production scanner stores absolute paths — match that so the
        # display-path stripping logic is exercised end-to-end.
        path=str(other_file),
        language_ext="py",
        start_line=1,
        end_line=12,
        content=(
            "def parse_price(value: str) -> int:\n"
            "    # helper used to hook up my_project pricing logic\n"
            "    return int(value)"
        ),
        symbol="parse_price",
        updated_at=0.0,
    )
    with CodeIndex() as idx:
        idx.upsert_blocks(str(other_repo), str(other_file), [block])

    # The cache also needs to know about the *other* repo for steal's
    # status-aware ranking lookup; otherwise it falls back to path basename.
    with Cache() as cache:
        cache.upsert(
            [
                Project(
                    path=other_repo,
                    name="invoicer",
                    type=ProjectType.GIT,
                    umbrella=tmp_path,
                    last_modified=datetime(2026, 5, 4, 12, 0, 0),
                    metadata=ProjectMetadata(status=Status.ACTIVE),
                ),
            ]
        )

    # 4) Stub the revive CLI — only this subprocess call is mocked.
    monkeypatch.setattr(
        "armillary.revive_enhanced.revive_show",
        lambda path, timeout=5.0: "# my_project brief\nPURPOSE: tests.\n",
    )

    out = armillary_revive(str(target_repo))

    # Vanilla brief preserved verbatim.
    assert out.startswith("# my_project brief\n")
    # STEAL_HITS section emitted because real CodeIndex returned the block.
    assert "## STEAL_HITS — code you wrote in other repos" in out
    # Display path is repo-relative even though the indexed CodeBlock
    # stores an absolute path under invoicer/.
    assert "invoicer/src/price.py:1-12 — parse_price" in out
    # Sanity: the absolute file path does NOT leak anywhere in the
    # rendered markdown (would mean the prefix-strip silently failed).
    assert str(other_file) not in out
    assert "def parse_price" in out
    # Anti-jargon spot-check (Harry's panel rule).
    for forbidden in ("graph", "context-aware", "intelligent", "knowledge base"):
        assert forbidden not in out.lower()


def test_armillary_revive_e2e_realistic_miss_emits_only_vanilla_brief(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Realistic miss: project name does NOT appear in any indexed block,
    so STEAL_HITS is omitted.

    This is the negative-path counterpart to the happy-path e2e. v0.1
    uses project_name as the query, and FTS only matches when that
    token is present in the indexed content. A project with a generic
    name and an indexed sibling that happens to share zero tokens is
    a typical case — and the brief survives intact without a hollow
    STEAL_HITS header.
    """
    from armillary.cache import Cache
    from armillary.code_block_service import CodeBlock
    from armillary.code_index import CodeIndex
    from armillary.mcp_server import armillary_revive
    from armillary.models import Project, ProjectMetadata, ProjectType, Status

    monkeypatch.setenv("ARMILLARY_CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.setenv("ARMILLARY_CODE_INDEX_DB", str(tmp_path / "code_index.db"))

    # Project to revive — name is unique enough to not collide.
    target_repo = tmp_path / "stripe-checkout"
    target_repo.mkdir()

    # Unrelated other repo: math helper whose content does NOT contain
    # the token "stripe-checkout" anywhere.
    other_repo = tmp_path / "csv-parser"
    other_repo.mkdir()
    block = CodeBlock(
        repo_path=str(other_repo),
        path="src/math.py",
        language_ext="py",
        start_line=1,
        end_line=10,
        content="def double(value: int) -> int:\n    return value + value\n",
        symbol="double",
        updated_at=0.0,
    )
    with CodeIndex() as idx:
        idx.upsert_blocks(str(other_repo), "src/math.py", [block])

    with Cache() as cache:
        cache.upsert(
            [
                Project(
                    path=target_repo,
                    name="stripe-checkout",
                    type=ProjectType.GIT,
                    umbrella=tmp_path,
                    last_modified=datetime(2026, 5, 4, 12, 0, 0),
                    metadata=ProjectMetadata(status=Status.ACTIVE),
                ),
                Project(
                    path=other_repo,
                    name="csv-parser",
                    type=ProjectType.GIT,
                    umbrella=tmp_path,
                    last_modified=datetime(2026, 5, 4, 12, 0, 0),
                    metadata=ProjectMetadata(status=Status.ACTIVE),
                ),
            ]
        )

    monkeypatch.setattr(
        "armillary.revive_enhanced.revive_show",
        lambda path, timeout=5.0: "# stripe-checkout brief\nPURPOSE: payments.\n",
    )

    out = armillary_revive(str(target_repo))

    # Brief preserved verbatim …
    assert out == "# stripe-checkout brief\nPURPOSE: payments.\n"
    # … and the STEAL_HITS section is intentionally absent because no
    # indexed block matches the project_name query.
    assert "STEAL_HITS" not in out
    assert "double" not in out
    assert "csv-parser" not in out

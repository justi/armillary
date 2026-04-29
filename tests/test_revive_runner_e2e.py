"""End-to-end tests for `revive_runner` — spawns the real `claude` and
`revive` binaries.

These tests are opt-in: they cost real money (a few cents per run via
the user's Anthropic key) and take ~30s–2min wallclock. CI excludes
them by default via `addopts = "-m 'not e2e'"` in pyproject.toml.

Run locally with:
    .venv/bin/python -m pytest -m e2e -q

Each test creates a sandbox tmp git repo with a clean working tree,
invokes `generate_brief()`, and asserts on the side effects (file
contents, backup lifecycle, success flag). The sandbox is fully
isolated — nothing outside `tmp_path` is touched.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from armillary.revive_runner import (
    accept_proposal,
    claude_available,
    generate_brief,
    git_is_clean,
    reject_proposal,
)

pytestmark = pytest.mark.e2e


def _require_binaries() -> None:
    if not claude_available():
        pytest.skip("`claude` CLI not on PATH")
    if shutil.which("revive") is None:
        pytest.skip("`revive` CLI not on PATH")


def _make_sandbox_repo(tmp_path: Path) -> Path:
    """Create a tiny git repo with one committed README and clean tree."""
    repo = tmp_path / "sandbox"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text(
        "# Sandbox\n\nA tiny throwaway project used to e2e-test "
        "armillary's revive runner.\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


def _scaffold_static_md(repo: Path) -> Path:
    """Pre-create .revive/static.md with a placeholder so `revive suggest`
    has something to anchor on. Mimics what `revive init` would scaffold."""
    static = repo / ".revive" / "static.md"
    static.parent.mkdir(parents=True, exist_ok=True)
    static.write_text(
        "PURPOSE: (run `revive init` to scaffold .revive/static.md)\n"
        "DIFFERENTIATORS:\n"
        "INVARIANTS:\n"
        "GOTCHAS:\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "scaffold revive"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return static


def test_e2e_dirty_tree_skipped(tmp_path: Path) -> None:
    """Dirty working tree must short-circuit BEFORE spawning claude."""
    _require_binaries()
    repo = _make_sandbox_repo(tmp_path)
    # Dirty the tree.
    (repo / "dirty.txt").write_text("dirty", encoding="utf-8")
    assert git_is_clean(repo) is False

    result = generate_brief(repo, timeout=10.0)

    assert result.success is False
    assert result.skipped_reason == "dirty_tree"
    assert result.before == ""
    assert result.after == ""
    # No backup should have been created.
    assert not (repo / ".revive" / "static.md.bak").exists()


def test_e2e_happy_path_proposes_diff_and_accept(tmp_path: Path) -> None:
    """Real claude run: produces non-empty diff, accept removes backup."""
    _require_binaries()
    repo = _make_sandbox_repo(tmp_path)
    static = _scaffold_static_md(repo)
    before = static.read_text(encoding="utf-8")

    result = generate_brief(repo, timeout=240.0)

    assert result.skipped_reason is None, f"skipped: {result.skipped_reason}"
    assert result.success is True, f"error: {result.error}"
    assert result.before == before
    assert result.after != before, "claude made no edits to static.md"
    assert result.diff, "diff should be non-empty when after != before"
    # On non-empty diff with prior content, backup is preserved until accept.
    backup = repo / ".revive" / "static.md.bak"
    assert backup.exists(), "backup must exist before accept_proposal"

    accept_proposal(repo)
    assert not backup.exists(), "accept_proposal must delete the backup"
    # Live file matches the captured `after`.
    assert static.read_text(encoding="utf-8") == result.after


def test_e2e_reject_restores_original(tmp_path: Path) -> None:
    """Real claude run + reject_proposal restores prior static.md."""
    _require_binaries()
    repo = _make_sandbox_repo(tmp_path)
    static = _scaffold_static_md(repo)
    before = static.read_text(encoding="utf-8")

    result = generate_brief(repo, timeout=240.0)
    assert result.success is True, f"error: {result.error}"
    assert result.diff, "diff should be non-empty for this test to be meaningful"

    reject_proposal(repo)

    assert static.read_text(encoding="utf-8") == before, (
        "reject_proposal must restore static.md to its pre-run content"
    )
    assert not (repo / ".revive" / "static.md.bak").exists(), (
        "reject_proposal must clean up the backup file"
    )

"""Tests for `armillary.revive_runner`."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from armillary.revive_runner import (
    accept_proposal,
    claude_available,
    generate_brief,
    git_is_clean,
    reject_proposal,
)


def _result(
    *, stdout: str = "", stderr: str = "", returncode: int = 0
) -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _patch_binaries(
    monkeypatch: pytest.MonkeyPatch,
    *,
    claude: str | None = "/tmp/bin/claude",
    revive: str | None = "/tmp/bin/revive",
) -> None:
    monkeypatch.setattr(
        "armillary.revive_runner.shutil.which",
        lambda name: {"claude": claude, "revive": revive}.get(name),
    )


@pytest.fixture(autouse=True)
def no_real_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("subprocess.run was not patched for this test")

    monkeypatch.setattr("armillary.revive_runner.subprocess.run", fail)


def test_claude_available_reflects_shutil_which(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_binaries(monkeypatch, claude="/tmp/bin/claude")
    assert claude_available() is True
    _patch_binaries(monkeypatch, claude=None)
    assert claude_available() is False


def test_git_is_clean_returns_true_for_clean_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        "armillary.revive_runner.subprocess.run",
        lambda *args, **kwargs: _result(stdout="", returncode=0),
    )

    assert git_is_clean(tmp_path) is True


def test_generate_brief_skips_when_claude_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_binaries(monkeypatch, claude=None)

    result = generate_brief(tmp_path)

    assert result.success is False
    assert result.skipped_reason == "claude_missing"
    assert result.before == ""
    assert result.after == ""
    assert result.diff == ""
    assert result.error is None


def test_generate_brief_skips_when_revive_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_binaries(monkeypatch, revive=None)

    result = generate_brief(tmp_path)

    assert result.success is False
    assert result.skipped_reason == "revive_missing"
    assert result.error is None


def test_generate_brief_skips_when_project_is_not_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_binaries(monkeypatch)

    result = generate_brief(tmp_path)

    assert result.success is False
    assert result.skipped_reason == "not_git_repo"


def test_generate_brief_refuses_dirty_tree_without_backup_or_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_binaries(monkeypatch)
    (tmp_path / ".git").mkdir()
    static_path = tmp_path / ".revive" / "static.md"
    static_path.parent.mkdir(parents=True)
    static_path.write_text("before\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        assert cmd == ["git", "status", "--porcelain"]
        return _result(stdout=" M tracked.txt\n", returncode=0)

    monkeypatch.setattr("armillary.revive_runner.subprocess.run", fake_run)

    result = generate_brief(tmp_path)

    assert result.success is False
    assert result.skipped_reason == "dirty_tree"
    assert calls == [["git", "status", "--porcelain"]]
    assert not (tmp_path / ".revive" / "static.md.bak").exists()


def test_generate_brief_returns_error_when_revive_suggest_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_binaries(monkeypatch)
    (tmp_path / ".git").mkdir()
    static_path = tmp_path / ".revive" / "static.md"
    static_path.parent.mkdir(parents=True)
    static_path.write_text("before\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "status", "--porcelain"]:
            return _result(stdout="", returncode=0)
        if cmd == ["/tmp/bin/revive", "suggest"]:
            return _result(stderr=" bad prompt \n", returncode=1)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("armillary.revive_runner.subprocess.run", fake_run)

    result = generate_brief(tmp_path)

    assert result.success is False
    assert result.error == "revive suggest failed: bad prompt"
    assert calls == [
        ["git", "status", "--porcelain"],
        ["/tmp/bin/revive", "suggest"],
    ]
    assert not (tmp_path / ".revive" / "static.md.bak").exists()


def test_generate_brief_restores_backup_when_claude_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_binaries(monkeypatch)
    (tmp_path / ".git").mkdir()
    static_path = tmp_path / ".revive" / "static.md"
    static_path.parent.mkdir(parents=True)
    static_path.write_text("before\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "status", "--porcelain"]:
            return _result(stdout="", returncode=0)
        if cmd == ["/tmp/bin/revive", "suggest"]:
            return _result(stdout="prompt", returncode=0)
        if cmd[0] == "claude":
            static_path.write_text("partial\n", encoding="utf-8")
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=12.5)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("armillary.revive_runner.subprocess.run", fake_run)

    result = generate_brief(tmp_path, timeout=12.5)

    assert result.success is False
    assert result.error == "claude timed out after 12.5s"
    assert result.before == "before\n"
    assert result.after == "before\n"
    assert static_path.read_text(encoding="utf-8") == "before\n"
    assert not (tmp_path / ".revive" / "static.md.bak").exists()


def test_generate_brief_restores_backup_when_claude_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_binaries(monkeypatch)
    (tmp_path / ".git").mkdir()
    static_path = tmp_path / ".revive" / "static.md"
    static_path.parent.mkdir(parents=True)
    static_path.write_text("before\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "status", "--porcelain"]:
            return _result(stdout="", returncode=0)
        if cmd == ["/tmp/bin/revive", "suggest"]:
            return _result(stdout="prompt", returncode=0)
        if cmd[0] == "claude":
            static_path.write_text("partial\n", encoding="utf-8")
            return _result(stderr=" boom \n", returncode=9)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("armillary.revive_runner.subprocess.run", fake_run)

    result = generate_brief(tmp_path)

    assert result.success is False
    assert result.error == "claude exited with 9: boom"
    assert result.before == "before\n"
    assert result.after == "before\n"
    assert static_path.read_text(encoding="utf-8") == "before\n"
    assert not (tmp_path / ".revive" / "static.md.bak").exists()


def test_generate_brief_returns_diff_and_keeps_backup_on_changed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_binaries(monkeypatch)
    (tmp_path / ".git").mkdir()
    static_path = tmp_path / ".revive" / "static.md"
    static_path.parent.mkdir(parents=True)
    static_path.write_text("before\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "status", "--porcelain"]:
            return _result(stdout="", returncode=0)
        if cmd == ["/tmp/bin/revive", "suggest"]:
            return _result(stdout="prompt", returncode=0)
        if cmd[0] == "claude":
            static_path.write_text("after\n", encoding="utf-8")
            return _result(returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("armillary.revive_runner.subprocess.run", fake_run)

    result = generate_brief(tmp_path)

    assert result.success is True
    assert result.before == "before\n"
    assert result.after == "after\n"
    assert result.diff == "--- before\n+++ after\n@@ -1 +1 @@\n-before\n+after\n"
    assert (tmp_path / ".revive" / "static.md.bak").read_text(encoding="utf-8") == (
        "before\n"
    )


def test_generate_brief_handles_new_static_file_without_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_binaries(monkeypatch)
    (tmp_path / ".git").mkdir()
    static_path = tmp_path / ".revive" / "static.md"

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "status", "--porcelain"]:
            return _result(stdout="", returncode=0)
        if cmd == ["/tmp/bin/revive", "suggest"]:
            return _result(stdout="prompt", returncode=0)
        if cmd[0] == "claude":
            static_path.parent.mkdir(parents=True, exist_ok=True)
            static_path.write_text("new brief\n", encoding="utf-8")
            return _result(returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("armillary.revive_runner.subprocess.run", fake_run)

    result = generate_brief(tmp_path)

    assert result.success is True
    assert result.before == ""
    assert result.after == "new brief\n"
    assert result.diff == "--- before\n+++ after\n@@ -0,0 +1 @@\n+new brief\n"
    assert not (tmp_path / ".revive" / "static.md.bak").exists()


def test_generate_brief_deletes_backup_when_claude_makes_no_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_binaries(monkeypatch)
    (tmp_path / ".git").mkdir()
    static_path = tmp_path / ".revive" / "static.md"
    static_path.parent.mkdir(parents=True)
    static_path.write_text("same\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "status", "--porcelain"]:
            return _result(stdout="", returncode=0)
        if cmd == ["/tmp/bin/revive", "suggest"]:
            return _result(stdout="prompt", returncode=0)
        if cmd[0] == "claude":
            return _result(returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("armillary.revive_runner.subprocess.run", fake_run)

    result = generate_brief(tmp_path)

    assert result.success is True
    assert result.diff == ""
    assert result.before == "same\n"
    assert result.after == "same\n"
    assert not (tmp_path / ".revive" / "static.md.bak").exists()


def test_accept_proposal_deletes_backup_idempotently(tmp_path: Path) -> None:
    backup_path = tmp_path / ".revive" / "static.md.bak"
    backup_path.parent.mkdir(parents=True)
    backup_path.write_text("before\n", encoding="utf-8")

    accept_proposal(tmp_path)
    accept_proposal(tmp_path)

    assert not backup_path.exists()


def test_reject_proposal_restores_backup_and_is_idempotent(tmp_path: Path) -> None:
    static_path = tmp_path / ".revive" / "static.md"
    backup_path = tmp_path / ".revive" / "static.md.bak"
    static_path.parent.mkdir(parents=True)
    static_path.write_text("after\n", encoding="utf-8")
    backup_path.write_text("before\n", encoding="utf-8")

    reject_proposal(tmp_path)
    reject_proposal(tmp_path)

    assert static_path.read_text(encoding="utf-8") == "before\n"
    assert not backup_path.exists()


def test_generate_brief_builds_expected_claude_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_binaries(monkeypatch)
    (tmp_path / ".git").mkdir()
    static_path = tmp_path / ".revive" / "static.md"
    seen_claude: list[str] | None = None

    def fake_run(cmd, **kwargs):
        nonlocal seen_claude
        if cmd == ["git", "status", "--porcelain"]:
            return _result(stdout="", returncode=0)
        if cmd == ["/tmp/bin/revive", "suggest"]:
            return _result(stdout="prompt text", returncode=0)
        if cmd[0] == "claude":
            seen_claude = cmd
            static_path.parent.mkdir(parents=True, exist_ok=True)
            static_path.write_text("new\n", encoding="utf-8")
            return _result(returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("armillary.revive_runner.subprocess.run", fake_run)

    generate_brief(tmp_path, max_turns=25)

    assert seen_claude == [
        "claude",
        "-p",
        "prompt text",
        "--allowed-tools",
        "Read,Edit,Write",
        "--max-turns",
        "10",
    ]


def test_generate_brief_uses_project_path_as_cwd_for_revive_and_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_binaries(monkeypatch)
    (tmp_path / ".git").mkdir()
    static_path = tmp_path / ".revive" / "static.md"
    seen: dict[str, Path] = {}

    def fake_run(cmd, **kwargs):
        cwd = kwargs["cwd"]
        if cmd == ["git", "status", "--porcelain"]:
            return _result(stdout="", returncode=0)
        if cmd == ["/tmp/bin/revive", "suggest"]:
            seen["revive"] = cwd
            return _result(stdout="prompt", returncode=0)
        if cmd[0] == "claude":
            seen["claude"] = cwd
            static_path.parent.mkdir(parents=True, exist_ok=True)
            static_path.write_text("new\n", encoding="utf-8")
            return _result(returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("armillary.revive_runner.subprocess.run", fake_run)

    generate_brief(tmp_path)

    assert seen == {"revive": tmp_path.resolve(), "claude": tmp_path.resolve()}


def test_generate_brief_writes_backup_only_to_expected_project_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_binaries(monkeypatch)
    (tmp_path / ".git").mkdir()
    static_path = tmp_path / ".revive" / "static.md"
    backup_path = tmp_path / ".revive" / "static.md.bak"
    static_path.parent.mkdir(parents=True)
    static_path.write_text("before\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "status", "--porcelain"]:
            return _result(stdout="", returncode=0)
        if cmd == ["/tmp/bin/revive", "suggest"]:
            return _result(stdout="prompt", returncode=0)
        if cmd[0] == "claude":
            static_path.write_text("after\n", encoding="utf-8")
            return _result(returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("armillary.revive_runner.subprocess.run", fake_run)

    generate_brief(tmp_path)

    assert backup_path.exists()
    assert backup_path.read_text(encoding="utf-8") == "before\n"
    assert not (tmp_path / "static.md.bak").exists()

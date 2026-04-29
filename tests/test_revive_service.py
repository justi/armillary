"""Tests for `armillary.revive_service`."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from armillary.revive_service import (
    ReviveError,
    copy_to_clipboard,
    generate_audit_prompt,
    generate_suggest_prompt,
    generate_suggest_prompts,
    install_hook_global,
    launch_claude_yolo,
    probe_capability,
    project_status,
    revive_show,
    run_revive_init,
)


@pytest.fixture(autouse=True)
def clear_probe_cache() -> None:
    probe_capability.cache_clear()
    yield
    probe_capability.cache_clear()


def _result(
    *, stdout: str = "", stderr: str = "", returncode: int = 0
) -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _write_hook_settings(base: Path, command: str) -> None:
    settings = base / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        (
            '{"hooks":{"UserPromptSubmit":['
            '{"hooks":[{"type":"command","command":"'
            f"{command}"
            '"}]}]}}'
        ),
        encoding="utf-8",
    )


def test_probe_capability_missing_binary_returns_neutral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("armillary.revive_service.shutil.which", lambda _: None)

    capability = probe_capability()

    assert capability.binary_available is False
    assert capability.binary_path is None
    assert capability.compatible is False
    assert capability.version is None


def test_probe_capability_marks_incompatible_when_help_is_missing_subcommands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )
    monkeypatch.setattr(
        "armillary.revive_service.subprocess.run",
        lambda *args, **kwargs: _result(
            stdout="revive 1.2.3\ncommands: show suggest version\n", returncode=0
        ),
    )

    capability = probe_capability()

    assert capability.binary_available is True
    assert capability.binary_path == Path("/tmp/bin/revive")
    assert capability.compatible is False
    assert capability.version == "1.2.3"


def test_probe_capability_cache_reuses_first_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_run(cmd, **kwargs):
        nonlocal calls
        calls += 1
        assert cmd == ["/tmp/bin/revive", "--help"]
        return _result(
            stdout=("revive 2.0.0\nshow\ninstall-hook\ndoctor\ninit\nsuggest\naudit\n"),
            returncode=0,
        )

    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )
    monkeypatch.setattr("armillary.revive_service.subprocess.run", fake_run)

    first = probe_capability()
    second = probe_capability()

    assert first == second
    assert first.compatible is True
    assert first.version == "2.0.0"
    assert calls == 1


def test_project_status_without_revive_dir_is_missing(tmp_path: Path) -> None:
    status = project_status(tmp_path, home=tmp_path / "home")

    assert status.static_exists is False
    assert status.brief_state == "missing"
    assert status.purpose_line is None
    assert status.static_path is None
    assert status.hook_scope == "none"
    assert status.last_modified is None


def test_project_status_populates_last_modified_when_static_exists(
    tmp_path: Path,
) -> None:
    """The UI shows the brief's age inline so users do not have to click
    Preview just to know how stale it is."""
    import os
    from datetime import datetime

    static_path = tmp_path / ".revive" / "static.md"
    static_path.parent.mkdir(parents=True)
    static_path.write_text(
        "PURPOSE: demo\nINVARIANTS:\n  - x\nGOTCHAS:\n  - y\n",
        encoding="utf-8",
    )
    # Pin mtime to a known instant so the assertion is deterministic.
    pinned = datetime(2026, 4, 1, 12, 0, 0).timestamp()
    os.utime(static_path, (pinned, pinned))

    status = project_status(tmp_path, home=tmp_path / "home")

    assert status.last_modified is not None
    assert status.last_modified.timestamp() == pinned


def test_project_status_placeholder_brief(tmp_path: Path) -> None:
    static_path = tmp_path / ".revive" / "static.md"
    static_path.parent.mkdir(parents=True)
    static_path.write_text(
        "# STATIC\nPURPOSE: (run `revive init` to scaffold .revive/static.md)\n",
        encoding="utf-8",
    )

    status = project_status(tmp_path, home=tmp_path / "home")

    assert status.static_exists is True
    assert status.brief_state == "placeholder"
    assert status.purpose_line == "(run `revive init` to scaffold .revive/static.md)"
    assert status.static_path == static_path


def test_project_status_configured_brief_sets_purpose_line(tmp_path: Path) -> None:
    static_path = tmp_path / ".revive" / "static.md"
    static_path.parent.mkdir(parents=True)
    static_path.write_text(
        "# STATIC\n"
        "PURPOSE: Local-first memory layer for solo devs\n"
        "INVARIANTS:\n"
        "  - No migrations: drop and rebuild.\n"
        "GOTCHAS:\n"
        "  - CI runs ruff format --check.\n",
        encoding="utf-8",
    )

    status = project_status(tmp_path, home=tmp_path / "home")

    assert status.static_exists is True
    assert status.brief_state == "configured"
    assert status.purpose_line == "Local-first memory layer for solo devs"
    assert status.static_path == static_path


def test_project_status_stub_when_invariants_and_gotchas_empty(tmp_path: Path) -> None:
    """`revive init` filled PURPOSE but the suggest/LLM step never ran."""
    static_path = tmp_path / ".revive" / "static.md"
    static_path.parent.mkdir(parents=True)
    static_path.write_text(
        "PURPOSE: A Rails 8 app converting PDFs to quizzes.\nINVARIANTS:\nGOTCHAS:\n",
        encoding="utf-8",
    )

    status = project_status(tmp_path, home=tmp_path / "home")

    assert status.brief_state == "stub"
    assert status.purpose_line == "A Rails 8 app converting PDFs to quizzes."


def test_project_status_configured_when_only_one_section_has_bullets(
    tmp_path: Path,
) -> None:
    """Stub state requires BOTH invariants and gotchas to be empty."""
    static_path = tmp_path / ".revive" / "static.md"
    static_path.parent.mkdir(parents=True)
    static_path.write_text(
        "PURPOSE: Demo project.\nINVARIANTS:\n  - Real invariant.\nGOTCHAS:\n",
        encoding="utf-8",
    )

    status = project_status(tmp_path, home=tmp_path / "home")

    assert status.brief_state == "configured"


def test_project_status_unreadable_static_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static_path = tmp_path / ".revive" / "static.md"
    static_path.parent.mkdir(parents=True)
    static_path.write_text("# STATIC\nPURPOSE: Hidden\n", encoding="utf-8")
    original_read_text = Path.read_text

    def fake_read_text(self: Path, *args, **kwargs) -> str:
        if self == static_path:
            raise PermissionError("denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    status = project_status(tmp_path, home=tmp_path / "home")

    assert status.static_exists is True
    assert status.brief_state == "unknown"
    assert status.purpose_line is None
    assert status.static_path == static_path


def test_project_status_detects_project_hook(tmp_path: Path) -> None:
    _write_hook_settings(
        tmp_path, "/Users/example/.local/bin/revive refresh --project-only"
    )

    status = project_status(tmp_path, home=tmp_path / "home")

    assert status.hook_scope == "project"


def test_project_status_detects_global_hook_with_default_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _write_hook_settings(home, "/Users/example/.local/bin/revive refresh")
    monkeypatch.setattr("armillary.revive_service.Path.home", lambda: home)

    status = project_status(tmp_path)

    assert status.hook_scope == "global"


def test_project_status_detects_both_hook_scopes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_hook_settings(tmp_path, "revive refresh --project")
    _write_hook_settings(home, "revive refresh --global")

    status = project_status(tmp_path, home=home)

    assert status.hook_scope == "both"


def test_project_status_malformed_hook_json_is_ignored(tmp_path: Path) -> None:
    project_settings = tmp_path / ".claude" / "settings.json"
    project_settings.parent.mkdir(parents=True, exist_ok=True)
    project_settings.write_text("{not-json", encoding="utf-8")
    home = tmp_path / "home"
    _write_hook_settings(home, "revive refresh --global")

    status = project_status(tmp_path, home=home)

    assert status.hook_scope == "global"


def test_revive_show_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=2.5)

    monkeypatch.setattr("armillary.revive_service.subprocess.run", fake_run)

    with pytest.raises(ReviveError, match=r"revive show timed out after 2.5s"):
        revive_show(Path("/tmp/project"), timeout=2.5)


def test_revive_show_nonzero_exit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )
    monkeypatch.setattr(
        "armillary.revive_service.subprocess.run",
        lambda *args, **kwargs: _result(stderr=" bad news \n", returncode=1),
    )

    with pytest.raises(ReviveError, match=r"revive show failed: bad news"):
        revive_show(Path("/tmp/project"))


def test_revive_show_missing_binary_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("armillary.revive_service.shutil.which", lambda _: None)

    with pytest.raises(ReviveError, match="revive binary not found on PATH"):
        revive_show(Path("/tmp/project"))


def test_install_hook_global_success_returns_combined_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )

    def fake_run(cmd, **kwargs):
        assert cmd == ["/tmp/bin/revive", "install-hook", "--global"]
        return _result(stdout="ok\n", stderr="warn\n", returncode=0)

    monkeypatch.setattr("armillary.revive_service.subprocess.run", fake_run)

    success, output = install_hook_global()

    assert success is True
    assert output == "ok\nwarn\n"


def test_install_hook_global_failure_returns_false_and_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )
    monkeypatch.setattr(
        "armillary.revive_service.subprocess.run",
        lambda *args, **kwargs: _result(
            stdout="partial\n", stderr="boom\n", returncode=1
        ),
    )

    success, output = install_hook_global()

    assert success is False
    assert output == "partial\nboom\n"


def test_install_hook_global_missing_binary_returns_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("armillary.revive_service.shutil.which", lambda _: None)

    success, output = install_hook_global()

    assert success is False
    assert output == "revive binary not found on PATH"


def test_generate_suggest_prompts_writes_slugged_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "alpha"
    project.mkdir()
    (project / ".git").mkdir()
    output_dir = tmp_path / "out"
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )

    def fake_run(cmd, *, cwd=None, **kwargs):
        assert cmd == ["/tmp/bin/revive", "suggest"]
        assert cwd == project
        assert kwargs["encoding"] == "utf-8"
        return _result(stdout="# prompt\n", returncode=0)

    monkeypatch.setattr("armillary.revive_service.subprocess.run", fake_run)

    written = generate_suggest_prompts([project], output_dir=output_dir)

    digest = hashlib.sha256(str(project).encode("utf-8")).hexdigest()[:8]
    expected = output_dir / f"alpha-{digest}.md"
    assert written == [expected]
    assert expected.read_text(encoding="utf-8") == "# prompt\n"


def test_generate_suggest_prompts_skips_non_git_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nongit = tmp_path / "notes"
    nongit.mkdir()
    timeout_repo = tmp_path / "timeout"
    timeout_repo.mkdir()
    (timeout_repo / ".git").mkdir()
    bad_repo = tmp_path / "bad"
    bad_repo.mkdir()
    (bad_repo / ".git").mkdir()
    good_repo = tmp_path / "good"
    good_repo.mkdir()
    (good_repo / ".git").mkdir()
    output_dir = tmp_path / "out"
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )

    def fake_run(cmd, *, cwd=None, **kwargs):
        if cwd == timeout_repo:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=5.0)
        if cwd == bad_repo:
            return _result(stderr="nope\n", returncode=1)
        if cwd == good_repo:
            return _result(stdout="usable\n", returncode=0)
        raise AssertionError(f"unexpected cwd: {cwd}")

    monkeypatch.setattr("armillary.revive_service.subprocess.run", fake_run)

    written = generate_suggest_prompts(
        [nongit, timeout_repo, bad_repo, good_repo],
        output_dir=output_dir,
    )

    assert [path.name for path in written] == [
        f"good-{hashlib.sha256(str(good_repo).encode('utf-8')).hexdigest()[:8]}.md"
    ]
    assert written[0].read_text(encoding="utf-8") == "usable\n"


def test_generate_suggest_prompts_missing_binary_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("armillary.revive_service.shutil.which", lambda _: None)

    written = generate_suggest_prompts([tmp_path], output_dir=tmp_path / "out")

    assert written == []


def test_generate_suggest_prompt_returns_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )

    def fake_run(cmd, **kwargs):
        assert cmd == ["/tmp/bin/revive", "suggest"]
        assert kwargs["cwd"] == tmp_path
        return SimpleNamespace(stdout="prompt body\n", stderr="", returncode=0)

    monkeypatch.setattr("armillary.revive_service.subprocess.run", fake_run)

    assert generate_suggest_prompt(tmp_path) == "prompt body\n"


def test_generate_suggest_prompt_raises_on_missing_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("armillary.revive_service.shutil.which", lambda _: None)
    with pytest.raises(ReviveError, match="not found on PATH"):
        generate_suggest_prompt(tmp_path)


def test_generate_suggest_prompt_raises_on_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )
    monkeypatch.setattr(
        "armillary.revive_service.subprocess.run",
        lambda *_, **__: SimpleNamespace(stdout="", stderr="kaboom\n", returncode=1),
    )
    with pytest.raises(ReviveError, match="kaboom"):
        generate_suggest_prompt(tmp_path)


def test_generate_suggest_prompt_falls_back_to_stdout_when_stderr_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Some revive subcommands write diagnostics to stdout, not stderr.

    The error message should still be informative — never blank or just
    the exit code when something useful was printed.
    """
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )
    monkeypatch.setattr(
        "armillary.revive_service.subprocess.run",
        lambda *_, **__: SimpleNamespace(
            stdout="missing scaffold\n", stderr="", returncode=2
        ),
    )
    with pytest.raises(ReviveError, match="missing scaffold"):
        generate_suggest_prompt(tmp_path)


def test_generate_suggest_prompt_falls_back_to_exit_code_when_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )
    monkeypatch.setattr(
        "armillary.revive_service.subprocess.run",
        lambda *_, **__: SimpleNamespace(stdout="", stderr="", returncode=7),
    )
    with pytest.raises(ReviveError, match="exit code 7"):
        generate_suggest_prompt(tmp_path)


def test_generate_audit_prompt_invokes_audit_subcommand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )
    seen: list = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return SimpleNamespace(stdout="audit body\n", stderr="", returncode=0)

    monkeypatch.setattr("armillary.revive_service.subprocess.run", fake_run)

    assert generate_audit_prompt(tmp_path) == "audit body\n"
    assert seen == [["/tmp/bin/revive", "audit"]]


def test_generate_audit_prompt_timeout_wraps_into_revive_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr("armillary.revive_service.subprocess.run", fake_run)
    with pytest.raises(ReviveError, match="timed out"):
        generate_audit_prompt(tmp_path, timeout=1.0)


def test_run_revive_init_returns_success_and_combined_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )
    monkeypatch.setattr(
        "armillary.revive_service.subprocess.run",
        lambda *_, **__: SimpleNamespace(
            stdout="created: .revive/static.md\n", stderr="", returncode=0
        ),
    )

    success, output = run_revive_init(tmp_path)

    assert success is True
    assert "created" in output


def test_run_revive_init_returns_false_on_missing_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("armillary.revive_service.shutil.which", lambda _: None)

    success, output = run_revive_init(tmp_path)

    assert success is False
    assert "not found on PATH" in output


def test_run_revive_init_returns_false_on_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )
    monkeypatch.setattr(
        "armillary.revive_service.subprocess.run",
        lambda *_, **__: SimpleNamespace(
            stdout="", stderr="cannot scaffold\n", returncode=1
        ),
    )

    success, output = run_revive_init(tmp_path)

    assert success is False
    assert "cannot scaffold" in output


def test_copy_to_clipboard_pipes_text_to_pbcopy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("armillary.revive_service.subprocess.run", fake_run)

    assert copy_to_clipboard("hello") is True
    assert captured["cmd"] == ["pbcopy"]
    assert captured["input"] == b"hello"


def test_copy_to_clipboard_returns_false_when_pbcopy_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("pbcopy")

    monkeypatch.setattr("armillary.revive_service.subprocess.run", fake_run)

    assert copy_to_clipboard("anything") is False


def test_launch_claude_yolo_invokes_osascript_with_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/usr/bin/osascript"
    )
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("armillary.revive_service.subprocess.run", fake_run)

    success, _ = launch_claude_yolo(tmp_path)

    assert success is True
    assert captured["cmd"][0] == "osascript"
    write_text_arg = next(
        arg for arg in captured["cmd"] if arg.startswith("write text")
    )
    assert str(tmp_path) in write_text_arg
    assert "claude --dangerously-skip-permissions" in write_text_arg


def test_launch_claude_yolo_returns_false_without_osascript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("armillary.revive_service.shutil.which", lambda _: None)

    success, message = launch_claude_yolo(tmp_path)

    assert success is False
    assert "osascript" in message


def test_launch_claude_yolo_returns_false_on_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/usr/bin/osascript"
    )
    monkeypatch.setattr(
        "armillary.revive_service.subprocess.run",
        lambda *_, **__: SimpleNamespace(
            stdout="", stderr="iTerm not running\n", returncode=1
        ),
    )

    success, message = launch_claude_yolo(tmp_path)

    assert success is False
    assert "iTerm" in message


def test_launch_claude_yolo_returns_false_when_claude_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `claude` is not on PATH, fail before opening the iTerm tab.

    Without the preflight the user sees a 🚀 success toast immediately
    followed by `command not found` in the new tab — confusing.
    """
    answers = {"osascript": "/usr/bin/osascript", "claude": None}
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which",
        lambda name: answers.get(name),
    )

    success, message = launch_claude_yolo(tmp_path)

    assert success is False
    assert "claude" in message.lower()


def test_launch_claude_yolo_quotes_path_with_spaces_and_metacharacters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repo paths with spaces, semicolons, $, or quotes must be safely
    embedded in the `cd` command and escaped for the AppleScript string."""
    weird = tmp_path / 'with "quoted" and spaces; rm -rf $HOME'
    weird.mkdir()
    monkeypatch.setattr("armillary.revive_service.shutil.which", lambda _: "/usr/bin/x")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("armillary.revive_service.subprocess.run", fake_run)

    success, _ = launch_claude_yolo(weird)

    assert success is True
    write_text_arg = next(
        arg for arg in captured["cmd"] if arg.startswith("write text")
    )
    # AppleScript wrapping: the inner string is enclosed in literal
    # double quotes; any literal " inside the path is escaped with \".
    body = write_text_arg.removeprefix("write text ")
    assert body.startswith('"') and body.endswith('"')
    assert '\\"quoted\\"' in body, "literal quotes must be backslash-escaped"
    # Shell-level: the `cd` argument is shlex-quoted (single quotes),
    # so the metacharacters land inside that single-quoted string and
    # cannot break the && claude tail.
    inner = body[1:-1]
    cd_part, _, claude_part = inner.partition(" && ")
    assert cd_part.startswith("cd '")
    assert cd_part.endswith("'")
    assert claude_part == "claude --dangerously-skip-permissions"


def test_probe_capability_requires_init_suggest_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe must mark revive incompatible if any of the subcommands
    armillary actually invokes (init / suggest / audit) is missing from
    --help, otherwise the UI offers buttons that fail only after click."""
    monkeypatch.setattr(
        "armillary.revive_service.shutil.which", lambda _: "/tmp/bin/revive"
    )
    monkeypatch.setattr(
        "armillary.revive_service.subprocess.run",
        lambda *_, **__: SimpleNamespace(
            stdout=(
                "revive 1.0.0\n"
                "Commands: show install-hook doctor\n"  # missing init/suggest/audit
            ),
            stderr="",
            returncode=0,
        ),
    )
    capability = probe_capability()
    assert capability.binary_available is True
    assert capability.compatible is False

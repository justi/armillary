"""CLI surface tests — Typer's CliRunner against `armillary.cli.app`.

These tests cover argument parsing, JSON shape, exit codes, and the
subprocess call for `start`. The underlying scanner logic is covered
by `test_scanner.py`; here we only verify the CLI wiring.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from armillary import (
    cli,
    cli_config,
    cli_config_ceremony,
    cli_helpers,
    cli_khoj,
    khoj_service,
)
from armillary.cache import Cache
from armillary.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_state(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redirect cache + config + Khoj probe to safe defaults.

    Three things every test gets for free:

    1. SQLite cache redirected to a per-test tmp file so `armillary scan`
       does not write to the user's real `~/Library/Application Support/
       armillary/cache.db`.
    2. `ARMILLARY_CONFIG` pointed at a non-existent path so the test never
       reads the developer's local umbrellas.
    3. `cli_config_ceremony.urlopen` stubbed to raise `URLError` so `armillary config
       --init` never accidentally probes the dev machine's localhost
       Khoj. Tests that exercise the Khoj detection path explicitly
       re-monkeypatch this attribute.

    Without #1+#2 a test like `test_scan_requires_umbrella_flag` would
    silently succeed because the developer's config has umbrellas. Without
    #3 a test running `--init` would block on a real network call to
    `localhost:42110` and either time out or pop a confirmation prompt
    depending on what's running.
    """
    from urllib.error import URLError as _URLError

    isolation_dir = tmp_path_factory.mktemp("armi-isolate")
    db_path = isolation_dir / "cache.db"
    config_path = isolation_dir / "missing-config.yaml"  # intentionally absent
    monkeypatch.setenv("ARMILLARY_CACHE_DB", str(db_path))
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_path))

    def _no_khoj(*args: Any, **kwargs: Any) -> Any:
        raise _URLError("test isolation: Khoj health probe disabled")

    monkeypatch.setattr(cli_config_ceremony, "urlopen", _no_khoj)


# Strips SGR / cursor control sequences from captured CLI output. Click and
# rich-based typer error rendering wrap option names in colour codes whenever
# they think the output is going to a terminal — which happens on GitHub
# Actions runners but not on a typical local pytest invocation. Substring
# assertions like `"max-depth" in stdout` would otherwise see
# `--\x1b[1;36mmax\x1b[0m\x1b[1;36m-depth\x1b[0m` and silently miss.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


# --- helpers ----------------------------------------------------------------


def _mkrepo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    return path


def _mkidea(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "notes.md").write_text("x")
    return path


# --- `armillary scan` -------------------------------------------------------


def test_scan_outputs_valid_json_for_empty_umbrella(tmp_path: Path) -> None:
    result = runner.invoke(app, ["scan", "-u", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout) == []


def test_scan_emits_one_entry_per_git_repo(tmp_path: Path) -> None:
    _mkrepo(tmp_path / "alpha")
    _mkrepo(tmp_path / "beta")

    result = runner.invoke(app, ["scan", "-u", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert {item["name"] for item in data} == {"alpha", "beta"}
    assert {item["type"] for item in data} == {"git"}


def test_scan_json_shape_has_expected_fields(tmp_path: Path) -> None:
    _mkrepo(tmp_path / "thing")

    result = runner.invoke(app, ["scan", "-u", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert len(data) == 1
    item = data[0]
    assert set(item) == {
        "path",
        "name",
        "type",
        "umbrella",
        "last_modified",
        "metadata",
    }
    assert item["type"] == "git"
    # last_modified must be ISO-8601 parseable
    from datetime import datetime

    datetime.fromisoformat(item["last_modified"])

    # M3.2: metadata is a dict (possibly with all-None fields when the
    # underlying repo is a fake `.git/` directory that GitPython cannot read).
    assert isinstance(item["metadata"], dict)
    assert set(item["metadata"]) >= {
        "branch",
        "last_commit_sha",
        "last_commit_ts",
        "last_commit_author",
        "dirty_count",
        "readme_excerpt",
        "adr_paths",
        "status",
    }


def test_scan_accepts_multiple_umbrellas(tmp_path: Path) -> None:
    a = tmp_path / "A"
    b = tmp_path / "B"
    _mkrepo(a / "one")
    _mkidea(b / "two")

    result = runner.invoke(app, ["scan", "-u", str(a), "-u", str(b)])

    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    by_name = {item["name"]: item for item in data}
    assert set(by_name) == {"one", "two"}
    assert by_name["one"]["type"] == "git"
    assert by_name["two"]["type"] == "idea"


def test_scan_respects_max_depth_flag(tmp_path: Path) -> None:
    deep = tmp_path / "lvl1" / "lvl2"
    deep.mkdir(parents=True)
    _mkrepo(deep / "repo")

    shallow = runner.invoke(app, ["scan", "-u", str(tmp_path), "--max-depth", "2"])
    deeper = runner.invoke(app, ["scan", "-u", str(tmp_path), "--max-depth", "3"])

    assert shallow.exit_code == 0 and deeper.exit_code == 0
    assert json.loads(shallow.stdout) == []
    assert [item["name"] for item in json.loads(deeper.stdout)] == ["repo"]


def test_scan_requires_umbrella_flag() -> None:
    result = runner.invoke(app, ["scan"])

    assert result.exit_code != 0
    # Typer/Click reports missing required option
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "umbrella" in combined.lower()


def test_scan_short_flags_match_long(tmp_path: Path) -> None:
    _mkrepo(tmp_path / "r")

    long = runner.invoke(app, ["scan", "--umbrella", str(tmp_path), "--max-depth", "3"])
    short = runner.invoke(app, ["scan", "-u", str(tmp_path), "-d", "3"])

    assert long.exit_code == 0 and short.exit_code == 0
    assert json.loads(long.stdout) == json.loads(short.stdout)


# --- regression: P3 (--max-depth bounds enforced at CLI boundary) ----------


@pytest.mark.parametrize("bad_value", ["0", "11", "-1", "999"])
def test_scan_rejects_max_depth_out_of_range(tmp_path: Path, bad_value: str) -> None:
    """Out-of-range --max-depth must produce a clean Click usage error,
    not a Pydantic ValidationError traceback from inside the command body.
    """
    result = runner.invoke(app, ["scan", "-u", str(tmp_path), "--max-depth", bad_value])

    assert result.exit_code != 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "Traceback" not in combined
    assert "ValidationError" not in combined
    # Click's IntRange error mentions the option name and the bound
    assert "max-depth" in combined.lower() or "max_depth" in combined.lower()


def test_scan_accepts_max_depth_at_bounds(tmp_path: Path) -> None:
    _mkrepo(tmp_path / "r")

    low = runner.invoke(app, ["scan", "-u", str(tmp_path), "--max-depth", "1"])
    high = runner.invoke(app, ["scan", "-u", str(tmp_path), "--max-depth", "10"])

    assert low.exit_code == 0, low.stdout
    assert high.exit_code == 0, high.stdout


# --- `armillary start` -----------------------------------------------------


def test_start_invokes_streamlit_with_default_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0, result.stdout
    cmd = captured["cmd"]
    assert "streamlit" in cmd
    assert "run" in cmd
    # default port flows through
    assert "8501" in cmd
    # ui/app.py is the streamlit entry point
    assert any(part.endswith("ui/app.py") for part in cmd)
    # default: browser is NOT headless
    assert "--server.headless" not in cmd


def test_start_passes_custom_port(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = runner.invoke(app, ["start", "--port", "9123"])

    assert result.exit_code == 0, result.stdout
    cmd = captured["cmd"]
    # port arg follows --server.port
    assert "--server.port" in cmd
    idx = cmd.index("--server.port")
    assert cmd[idx + 1] == "9123"


def test_start_no_browser_flag_sets_headless(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = runner.invoke(app, ["start", "--no-browser"])

    assert result.exit_code == 0, result.stdout
    cmd = captured["cmd"]
    assert "--server.headless" in cmd
    idx = cmd.index("--server.headless")
    assert cmd[idx + 1] == "true"


# --- Codex round 2: C (Streamlit telemetry off by default) -----------------


def test_start_disables_streamlit_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PLAN.md §14 promises no telemetry. Streamlit defaults
    `browser.gatherUsageStats` to true, so `start` must override it."""
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0, result.stdout
    cmd = captured["cmd"]
    assert "--browser.gatherUsageStats" in cmd
    idx = cmd.index("--browser.gatherUsageStats")
    assert cmd[idx + 1].lower() == "false"


# --- Codex round 2: D (start error handling) --------------------------------


def test_start_propagates_streamlit_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Streamlit dies (port in use, crash, ...), CLI must surface it."""

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        class _R:
            returncode = 7

        return _R()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 7


def test_start_errors_clearly_when_streamlit_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing streamlit module gives a Typer error, not an obscure
    `python -m streamlit` failure."""
    real_find_spec = cli.importlib.util.find_spec

    def fake_find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "streamlit":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(cli.importlib.util, "find_spec", fake_find_spec)

    # subprocess.run must NOT be called — guard with a sentinel that fails
    # the test if reached.
    def must_not_run(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("subprocess.run called despite missing streamlit")

    monkeypatch.setattr(cli.subprocess, "run", must_not_run)

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 2
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "streamlit" in combined.lower()
    assert "not installed" in combined.lower()


# --- placeholder commands (M3-M5) -------------------------------------------


# Note: the placeholder-command parametrized test is gone — every command
# (start, scan, list, search, open, config) is now real as of M6.


# --- M3.1: scan persists to cache, list reads back -------------------------


def test_scan_lifts_last_modified_to_last_commit_when_filesystem_is_older(
    tmp_path: Path,
) -> None:
    """Regression for the .git mtime bug — the cloned-old-repo case.

    A repo with backdated commit AND backdated file mtimes (simulating
    a long-untouched project) must report `last_modified ≈ commit time`
    instead of "scan time". The CLI lifts `last_modified` up to
    `last_commit_ts` whenever the metadata's commit time is newer than
    the filesystem signal.
    """
    import os as _os
    import subprocess as _sp
    from datetime import datetime as _dt

    repo = tmp_path / "ancient"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_AUTHOR_DATE": "2025-01-15T12:00:00",
        "GIT_COMMITTER_DATE": "2025-01-15T12:00:00",
        "PATH": _os.environ.get("PATH", ""),
    }
    _sp.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=env)
    (repo / "README.md").write_text("# ancient\n\nA test project.")
    _sp.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    _sp.run(["git", "commit", "-q", "-m", "old"], cwd=repo, check=True, env=env)

    # Backdate file + dir mtimes too, so the scanner's filesystem signal
    # is OLDER than the commit. Then last_commit_ts (Jan 2025) wins.
    old_ts = _dt(2024, 6, 1, 12, 0, 0).timestamp()
    _os.utime(repo / "README.md", (old_ts, old_ts))
    _os.utime(repo, (old_ts, old_ts))

    result = runner.invoke(app, ["scan", "-u", str(tmp_path)])
    assert result.exit_code == 0, result.stdout

    data = json.loads(result.stdout)
    assert len(data) == 1
    item = data[0]

    last_mod = _dt.fromisoformat(item["last_modified"])
    last_commit = _dt.fromisoformat(item["metadata"]["last_commit_ts"])
    # last_modified should reflect the (newer) commit time, not the
    # backdated filesystem mtime.
    assert last_mod == last_commit
    assert last_mod.year == 2025
    assert last_mod.month == 1


def test_scan_preserves_filesystem_last_modified_when_files_edited_after_commit(
    tmp_path: Path,
) -> None:
    """Regression for the Codex round-1 P2 finding on PR #9.

    If the user edits a file AFTER the last commit, `last_modified`
    must reflect the edit (not the older commit time). Otherwise the
    dashboard claims a busy repo is "weeks old" just because the user
    has not yet committed their work.
    """
    import os as _os
    import subprocess as _sp
    import time as _time
    from datetime import datetime as _dt

    repo = tmp_path / "edited"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_AUTHOR_DATE": "2025-01-15T12:00:00",
        "GIT_COMMITTER_DATE": "2025-01-15T12:00:00",
        "PATH": _os.environ.get("PATH", ""),
    }
    _sp.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=env)
    (repo / "README.md").write_text("# original")
    _sp.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    _sp.run(["git", "commit", "-q", "-m", "old"], cwd=repo, check=True, env=env)

    # Now edit a file — `now` is much newer than the 2025 commit.
    _time.sleep(0.05)
    (repo / "README.md").write_text("# edited after the old commit")

    result = runner.invoke(app, ["scan", "-u", str(tmp_path)])
    assert result.exit_code == 0, result.stdout

    data = json.loads(result.stdout)
    item = data[0]
    last_mod = _dt.fromisoformat(item["last_modified"])
    last_commit = _dt.fromisoformat(item["metadata"]["last_commit_ts"])

    # last_modified must reflect the edit ("today"), NOT collapse to commit time
    assert last_mod > last_commit
    # And must be roughly "now"
    age_seconds = (_dt.now() - last_mod).total_seconds()
    assert age_seconds < 30, f"Expected last_modified ≈ now, got {last_mod}"


def test_scan_no_metadata_keeps_filesystem_last_modified(
    tmp_path: Path,
) -> None:
    """With `--no-metadata`, no extraction happens so the override does
    not fire — the scanner's filesystem-based `last_modified` is the
    only signal we have. This test pins the contract."""
    _mkrepo(tmp_path / "thing")

    result = runner.invoke(app, ["scan", "-u", str(tmp_path), "--no-metadata"])
    assert result.exit_code == 0, result.stdout

    data = json.loads(result.stdout)
    assert data[0]["metadata"] is None
    # last_modified is whatever the scanner produced — we just check it
    # was set to *something* and not crashed.
    from datetime import datetime as _dt

    _dt.fromisoformat(data[0]["last_modified"])


def test_scan_persists_results_to_cache(tmp_path: Path) -> None:
    """A successful scan must populate the cache the next `list` reads."""
    _mkrepo(tmp_path / "alpha")
    _mkrepo(tmp_path / "beta")

    scan_result = runner.invoke(app, ["scan", "-u", str(tmp_path)])
    assert scan_result.exit_code == 0, scan_result.stdout

    with Cache() as cache:
        rows = cache.list_projects()
    assert {r.name for r in rows} == {"alpha", "beta"}


def test_scan_no_cache_skips_persistence(tmp_path: Path) -> None:
    """`--no-cache` must not write anything to disk."""
    _mkrepo(tmp_path / "alpha")

    result = runner.invoke(app, ["scan", "-u", str(tmp_path), "--no-cache"])
    assert result.exit_code == 0, result.stdout

    # The cache file may or may not exist depending on whether previous tests
    # touched it; what matters is that our project is NOT in there.
    with Cache() as cache:
        assert cache.count() == 0


def test_scan_json_output_unchanged_when_caching(tmp_path: Path) -> None:
    """Persisting must not alter what we print to stdout. The cache layer
    is invisible to anyone piping `armillary scan` into jq."""
    _mkrepo(tmp_path / "alpha")

    cached = runner.invoke(app, ["scan", "-u", str(tmp_path)])
    no_cache = runner.invoke(app, ["scan", "-u", str(tmp_path), "--no-cache"])

    assert cached.exit_code == 0 and no_cache.exit_code == 0
    assert json.loads(cached.stdout) == json.loads(no_cache.stdout)


def test_list_empty_cache_prints_hint() -> None:
    """`armillary list` against an empty cache tells the user to scan first."""
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "no projects in cache" in combined.lower()
    assert "armillary scan" in combined.lower()


def test_list_renders_table_after_scan(tmp_path: Path) -> None:
    _mkrepo(tmp_path / "repo-one")
    runner.invoke(app, ["scan", "-u", str(tmp_path)])

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    out = _strip_ansi(result.stdout)
    assert "repo-one" in out
    assert "git" in out
    # rich table heading reflects the count
    assert "1 project" in out


def test_list_filters_by_type(tmp_path: Path) -> None:
    _mkrepo(tmp_path / "real-repo")
    sketch = tmp_path / "sketch-pad"
    sketch.mkdir()
    (sketch / "notes.md").write_text("x")
    runner.invoke(app, ["scan", "-u", str(tmp_path)])

    git_only = _strip_ansi(runner.invoke(app, ["list", "--type", "git"]).stdout)
    idea_only = _strip_ansi(runner.invoke(app, ["list", "--type", "idea"]).stdout)

    assert "real-repo" in git_only
    assert "sketch-pad" not in git_only
    assert "sketch-pad" in idea_only
    assert "real-repo" not in idea_only


def test_list_filters_by_umbrella_substring(tmp_path: Path) -> None:
    work = tmp_path / "work"
    play = tmp_path / "play"
    _mkrepo(work / "alpha")
    _mkrepo(play / "beta")

    runner.invoke(app, ["scan", "-u", str(work), "-u", str(play)])

    result = runner.invoke(app, ["list", "--umbrella", "work"])
    out = _strip_ansi(result.stdout)
    assert "alpha" in out
    assert "beta" not in out


def test_list_rejects_invalid_type() -> None:
    """The --type filter is enum-validated by Click."""
    result = runner.invoke(app, ["list", "--type", "blueprint"])
    assert result.exit_code != 0


# --- M5: armillary open ---------------------------------------------------


def test_open_unknown_project_errors_out(tmp_path: Path) -> None:
    """No matching project in cache → error, no subprocess."""
    result = runner.invoke(app, ["open", "totally-fictional-name"])
    assert result.exit_code != 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "no project" in combined.lower()


def test_open_ambiguous_project_errors_out(tmp_path: Path) -> None:
    _mkrepo(tmp_path / "alpha-one")
    _mkrepo(tmp_path / "alpha-two")
    runner.invoke(app, ["scan", "-u", str(tmp_path)])

    result = runner.invoke(app, ["open", "alpha"])
    assert result.exit_code != 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "ambiguous" in combined.lower()
    assert "alpha-one" in combined or "alpha-two" in combined


def test_open_invokes_launcher_when_executable_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI passes the resolved project path to the launcher."""
    _mkrepo(tmp_path / "myrepo")
    runner.invoke(app, ["scan", "-u", str(tmp_path)])

    captured: dict[str, Any] = {}

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}"

    def fake_popen(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return None

    from armillary import launcher as launcher_mod

    monkeypatch.setattr(launcher_mod.shutil, "which", fake_which)
    monkeypatch.setattr(launcher_mod.subprocess, "Popen", fake_popen)

    result = runner.invoke(app, ["open", "myrepo", "--target", "cursor"])
    assert result.exit_code == 0, result.stdout
    assert captured["cmd"][0] == "cursor"
    assert "myrepo" in captured["cmd"][-1]
    assert "myrepo" in captured["cwd"]


def test_open_unknown_target_errors_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mkrepo(tmp_path / "thing")
    runner.invoke(app, ["scan", "-u", str(tmp_path)])

    # Even if the user passes a fictional target, no subprocess fires
    from armillary import launcher as launcher_mod

    def must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("subprocess.Popen should not be called")

    monkeypatch.setattr(launcher_mod.subprocess, "Popen", must_not_run)

    result = runner.invoke(app, ["open", "thing", "--target", "nope-editor"])
    assert result.exit_code != 0


# --- M5: armillary config -------------------------------------------------


def test_config_path_prints_default_location(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ARMILLARY_CONFIG", str(tmp_path / "config.yaml"))
    result = runner.invoke(app, ["config", "--path"])
    assert result.exit_code == 0
    assert str(tmp_path / "config.yaml") in result.stdout


def test_config_init_blank_creates_starter_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--init --blank` writes the placeholder YAML without scanning ~/."""
    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    # `$EDITOR` is intentionally set to a sentinel that would fail loudly
    # if `config --init` accidentally tried to open it. The fix below
    # ensures `--init` returns BEFORE the editor branch ever runs.
    monkeypatch.setenv("EDITOR", "definitely-not-a-real-editor")

    result = runner.invoke(app, ["config", "--init", "--blank"])
    assert result.exit_code == 0, result.stdout
    assert config_file.exists()
    assert "umbrellas" in config_file.read_text()


def test_config_init_does_not_open_editor_after_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression for the user-reported "init opens nano" bug.

    `armillary config --init` should write the file and exit cleanly.
    Falling through to `$EDITOR` is surprising — the user just chose
    what goes into the file, they do not expect nano to pop up.
    Use plain `armillary config` to edit afterwards.
    """
    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))
    # Sentinel: if the code falls through to the editor branch, this
    # value will trip the `which()` check and the test will fail loudly.
    monkeypatch.setenv("EDITOR", "definitely-not-a-real-editor")

    # Guard rail: subprocess.run must NOT be called for the editor.
    real_run = cli_config.subprocess.run

    def trapped_run(cmd: list[str], **kwargs: Any) -> Any:
        # The init flow itself never runs `subprocess.run`, so any call
        # here is the editor branch. Fail loudly with the cmd in the
        # assertion message so future regressions are obvious.
        raise AssertionError(f"config --init unexpectedly opened the editor: {cmd}")

    monkeypatch.setattr(cli_config.subprocess, "run", trapped_run)

    result = runner.invoke(app, ["config", "--init", "--blank"])
    assert result.exit_code == 0, result.stdout
    assert config_file.exists()
    # Sanity: the trap above means we definitely did not open EDITOR.
    del real_run


def test_config_init_when_file_exists_aborts_without_confirm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--init` against an existing file shows a confirm prompt. If the
    user says no (default on bare Enter), the original file is
    untouched and no backup is created."""
    config_file = tmp_path / "armillary" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("umbrellas: []\n")
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))
    monkeypatch.setenv("EDITOR", "true")

    # Guard rail: editor must not be called.
    def trapped_run(cmd: list[str], **kwargs: Any) -> Any:
        raise AssertionError(f"editor opened on init-with-existing: {cmd}")

    monkeypatch.setattr(cli_config.subprocess, "run", trapped_run)

    # Bare Enter → default N.
    result = runner.invoke(app, ["config", "--init", "--blank"], input="\n")
    assert result.exit_code != 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "already exists" in combined.lower()
    # File contents are unchanged, no backup yet.
    assert config_file.read_text() == "umbrellas: []\n"
    assert not config_file.with_suffix(".yaml.bak").exists()


def test_config_init_overwrites_on_confirm_yes_with_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Confirming the prompt with `y` backs up the original and then
    runs the normal init flow (here: --blank to keep the test fast)."""
    config_file = tmp_path / "armillary" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    original = "# my hand-edited config\numbrellas:\n  - path: /old\n"
    config_file.write_text(original)
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(
        app,
        ["config", "--init", "--blank"],
        input="y\n",
    )
    assert result.exit_code == 0, result.stdout

    # Backup has the original bytes exactly
    backup = config_file.with_suffix(".yaml.bak")
    assert backup.exists()
    assert backup.read_text() == original

    # Main file was rewritten (blank starter, not the hand-edited version)
    new_content = config_file.read_text()
    assert new_content != original
    assert "umbrellas" in new_content


def test_config_init_force_skips_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--force` overwrites without asking. Backup still written."""
    config_file = tmp_path / "armillary" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    original = "umbrellas:\n  - path: /old\n"
    config_file.write_text(original)
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    # No input — if a prompt fired we would hang / EOF to non-zero.
    result = runner.invoke(
        app,
        ["config", "--init", "--blank", "--force"],
    )
    assert result.exit_code == 0, result.stdout
    assert config_file.with_suffix(".yaml.bak").read_text() == original
    assert config_file.read_text() != original


def test_config_init_non_interactive_without_force_errors_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--non-interactive` without `--force` keeps the strict old
    behaviour: never silently overwrite a config in a script context."""
    config_file = tmp_path / "armillary" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("umbrellas: []\n")
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(
        app,
        ["config", "--init", "--blank", "--non-interactive"],
    )
    assert result.exit_code != 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "already exists" in combined.lower()
    assert "--force" in combined
    # File contents unchanged, no backup
    assert config_file.read_text() == "umbrellas: []\n"
    assert not config_file.with_suffix(".yaml.bak").exists()


def test_config_init_non_interactive_with_force_overwrites(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--non-interactive --force` is the explicit script opt-in:
    backs up and overwrites with no prompt."""
    config_file = tmp_path / "armillary" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    original = "umbrellas:\n  - path: /old\n"
    config_file.write_text(original)
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(
        app,
        ["config", "--init", "--blank", "--non-interactive", "--force"],
    )
    assert result.exit_code == 0, result.stdout
    assert config_file.with_suffix(".yaml.bak").read_text() == original


def test_config_init_non_interactive_uses_discovered_umbrellas(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--init --non-interactive` runs bootstrap discovery against a
    monkeypatched `Path.home()` and writes every detected candidate."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    # Two umbrella candidates: one with multiple git repos, one with a
    # conventional name (`projects_prod`).
    work = fake_home / "work"
    _mkrepo(work / "repo-a")
    _mkrepo(work / "repo-b")

    prod = fake_home / "projects_prod"
    _mkrepo(prod / "deploy")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))
    monkeypatch.setenv("EDITOR", "true")

    result = runner.invoke(app, ["config", "--init", "--non-interactive"])
    assert result.exit_code == 0, result.stdout
    assert config_file.exists()

    text = config_file.read_text()
    # Both candidates show up
    assert "work" in text
    assert "projects_prod" in text


def test_config_init_interactive_picker_accepts_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default `--init` mode prompts for a selection. Pressing Enter
    on the default ('all') accepts every candidate."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    work = fake_home / "work"
    _mkrepo(work / "alpha")
    _mkrepo(work / "beta")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))
    monkeypatch.setenv("EDITOR", "true")

    # CliRunner can pipe stdin via `input=`
    result = runner.invoke(app, ["config", "--init"], input="all\n")
    assert result.exit_code == 0, result.stdout
    assert config_file.exists()
    assert "work" in config_file.read_text()


def test_config_init_interactive_picker_accepts_specific_numbers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """User picks `1` from a multi-candidate list — only that one ends up
    in the file."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    # Two candidates so the order matters.
    a = fake_home / "Projects"  # conventional name → always passes
    _mkrepo(a / "alpha")
    b = fake_home / "code"  # also conventional
    _mkrepo(b / "bravo")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))
    monkeypatch.setenv("EDITOR", "true")

    # Pick just the first candidate (whatever sort order put it there)
    result = runner.invoke(app, ["config", "--init"], input="1\n")
    assert result.exit_code == 0, result.stdout
    text = config_file.read_text()
    # Only one umbrella entry written
    assert text.count("- path:") == 1


def test_config_init_picker_blank_input_actually_cancels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression for Codex review on PR #12: pressing Enter on the
    picker prompt must NOT silently accept all candidates."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    work = fake_home / "Projects"
    _mkrepo(work / "alpha")
    _mkrepo(work / "beta")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))
    monkeypatch.setenv("EDITOR", "true")

    # Press Enter (empty input) at the picker → cancel.
    result = runner.invoke(app, ["config", "--init"], input="\n")

    # Cancellation: exit non-zero and the file is NOT written.
    assert result.exit_code != 0
    assert not config_file.exists()
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "no umbrellas selected" in combined.lower()


def test_config_init_yaml_handles_special_characters_in_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression for Codex review on PR #12: folder names with YAML
    metacharacters (`#`, `:`) must round-trip through `_render_config_yaml`
    and `load_config` without truncation or parse errors."""
    import yaml as _yaml

    from armillary.config import load_config

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    # A folder name with a `#` would otherwise be parsed as a comment by
    # plain-scalar YAML.
    weird = fake_home / "Work #archive"
    _mkrepo(weird / "alpha")
    _mkrepo(weird / "beta")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))
    monkeypatch.setenv("EDITOR", "true")

    result = runner.invoke(app, ["config", "--init", "--non-interactive"])
    assert result.exit_code == 0, result.stdout

    raw = config_file.read_text()
    parsed = _yaml.safe_load(raw)
    assert parsed["umbrellas"][0]["label"] == "Work #archive"
    # Path should also survive — `_shorten_home_str` may have replaced
    # the home prefix with `~`, but the suffix must be intact.
    assert "Work #archive" in parsed["umbrellas"][0]["path"]

    # And `load_config` parses it without error.
    cfg = load_config(config_file)
    assert len(cfg.umbrellas) == 1
    assert "Work #archive" in str(cfg.umbrellas[0].path)


# --- PR #16: setup ceremony in `config --init` ----------------------------


def _fake_urlopen_200(*args: Any, **kwargs: Any) -> Any:
    """Fake urlopen returning a 200 response — for Khoj-detected tests."""
    from io import BytesIO

    class FakeResponse:
        status = 200

        def __init__(self) -> None:
            self._buf = BytesIO(b'{"status": "ok"}')

        def read(self) -> bytes:
            return self._buf.read()

        def getcode(self) -> int:
            return 200

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

    return FakeResponse()


def test_config_init_runs_initial_scan_and_populates_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Setup ceremony step 1: after writing YAML, run a real scan and
    populate the SQLite cache so the user can immediately
    `armillary list` / `armillary start` without a separate scan."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    work = fake_home / "Projects"
    _mkrepo(work / "alpha")
    _mkrepo(work / "beta")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(
        app,
        [
            "config",
            "--init",
            "--non-interactive",
            "--skip-khoj-detect",
            "--skip-claude-detect",
        ],
    )
    assert result.exit_code == 0, result.stdout

    # Cache must contain both projects after init
    with Cache() as cache:
        names = {p.name for p in cache.list_projects()}
    assert names == {"alpha", "beta"}

    out = _strip_ansi(result.stdout)
    assert "Indexed 2 project" in out


def test_config_init_summary_counts_status_correctly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Setup ceremony step 2: per-status summary line printed after scan."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    work = fake_home / "Projects"
    _mkrepo(work / "git-thing")
    idea = work / "idea-thing"
    idea.mkdir()
    (idea / "notes.md").write_text("notes")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(
        app,
        [
            "config",
            "--init",
            "--non-interactive",
            "--skip-khoj-detect",
            "--skip-claude-detect",
        ],
    )
    assert result.exit_code == 0, result.stdout

    out = _strip_ansi(result.stdout)
    assert "1 git, 1 idea" in out
    # Status line is "N ACTIVE, M PAUSED, ..."
    assert "ACTIVE" in out
    assert "DORMANT" in out or "IDEA" in out  # at least one status counted


def test_config_init_launcher_detection_lists_available_and_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Setup ceremony step 3: cross-checks `cfg.launchers` against
    `shutil.which` and prints which are reachable on PATH."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _mkrepo(fake_home / "Projects" / "thing")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    # Pretend `cursor` and `code` are on PATH but `zed` and `claude` are not.
    real_which = cli_helpers.shutil_which

    def fake_which(name: str) -> str | None:
        if name in {"cursor", "code", "open"}:
            return f"/usr/bin/{name}"
        return None

    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", fake_which)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(
        app,
        [
            "config",
            "--init",
            "--non-interactive",
            "--skip-khoj-detect",
            "--skip-claude-detect",
        ],
    )
    assert result.exit_code == 0, result.stdout

    out = _strip_ansi(result.stdout)
    assert "available" in out.lower()
    assert "cursor" in out
    assert "missing" in out.lower()
    assert "zed" in out or "claude" in out
    del real_which  # silence linter


def test_config_init_khoj_detection_auto_enables_when_health_responds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Setup ceremony step 4: when localhost Khoj responds 200, the YAML
    is auto-rewritten with `khoj.enabled: true` — no prompt. Users who
    have Khoj running almost certainly want it; the dashboard Settings
    page is the explicit opt-out."""
    import yaml as _yaml

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _mkrepo(fake_home / "Projects" / "thing")

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr(cli_config_ceremony, "urlopen", _fake_urlopen_200)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    # Picker accepts all. No Khoj prompt expected anymore.
    result = runner.invoke(
        app,
        [
            "config",
            "--init",
            "--skip-claude-detect",
        ],
        input="all\n",
    )
    assert result.exit_code == 0, result.stdout

    parsed = _yaml.safe_load(config_file.read_text())
    assert parsed.get("khoj", {}).get("enabled") is True

    out = _strip_ansi(result.stdout)
    assert "Detected Khoj" in out
    assert "Enabled semantic search" in out


def test_config_init_khoj_auto_enables_in_non_interactive_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-interactive mode also auto-enables a reachable Khoj — the
    whole point of auto-enable is that Khoj availability is a clear
    signal of user intent, no matter which init flavour ran."""
    import yaml as _yaml

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _mkrepo(fake_home / "Projects" / "thing")

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr(cli_config_ceremony, "urlopen", _fake_urlopen_200)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(
        app,
        [
            "config",
            "--init",
            "--non-interactive",
            "--skip-claude-detect",
        ],
    )
    assert result.exit_code == 0, result.stdout
    parsed = _yaml.safe_load(config_file.read_text())
    assert parsed.get("khoj", {}).get("enabled") is True


def test_config_init_khoj_detection_prints_install_hint_when_unreachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Setup ceremony step 4 negative: when Khoj health probe fails,
    init now prints an explicit "how to install" block pointing at
    `armillary install-khoj`. Silent-skip was user-hostile — people
    could not discover semantic search existed. The config still does
    NOT enable khoj (we never enable a service we could not reach)."""
    import yaml as _yaml

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _mkrepo(fake_home / "Projects" / "thing")

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    # Note: do NOT override the autouse `_no_khoj` stub.

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(
        app,
        [
            "config",
            "--init",
            "--non-interactive",
            "--skip-claude-detect",
        ],
    )
    assert result.exit_code == 0, result.stdout

    out = _strip_ansi(result.stdout)
    # "Detected Khoj" never appears because we did NOT detect it.
    assert "Detected Khoj" not in out
    # But the install hint IS visible and mentions both commands.
    assert "Khoj not detected" in out
    assert "install-khoj" in out
    assert "start-khoj" in out
    parsed = _yaml.safe_load(config_file.read_text())
    assert parsed.get("khoj") is None or not parsed["khoj"].get("enabled")


def test_config_init_khoj_non_200_response_is_treated_as_unreachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the Khoj endpoint responds but with a non-2xx status (e.g.
    503 from a service that's still warming up), auto-enable must NOT
    fire. Only a clean 200 counts as "this user has Khoj"."""
    import yaml as _yaml

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _mkrepo(fake_home / "Projects" / "thing")

    def _fake_urlopen_503(*args: Any, **kwargs: Any) -> Any:
        class FakeResponse:
            status = 503

            def getcode(self) -> int:
                return 503

            def __enter__(self) -> FakeResponse:
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

        return FakeResponse()

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr(cli_config_ceremony, "urlopen", _fake_urlopen_503)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(
        app,
        [
            "config",
            "--init",
            "--non-interactive",
            "--skip-claude-detect",
        ],
    )
    assert result.exit_code == 0, result.stdout

    out = _strip_ansi(result.stdout)
    # No "Detected Khoj" line — a 503 is not a detection.
    assert "Detected Khoj" not in out
    parsed = _yaml.safe_load(config_file.read_text())
    assert parsed.get("khoj") is None or not parsed["khoj"].get("enabled")


def test_config_init_skip_khoj_flag_skips_detection_entirely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Setup ceremony step 4 with --skip-khoj-detect: even if Khoj
    responds 200, no prompt appears and no enable happens."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _mkrepo(fake_home / "Projects" / "thing")

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr(cli_config_ceremony, "urlopen", _fake_urlopen_200)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(
        app,
        [
            "config",
            "--init",
            "--non-interactive",
            "--skip-khoj-detect",
            "--skip-claude-detect",
        ],
    )
    assert result.exit_code == 0, result.stdout

    out = _strip_ansi(result.stdout)
    assert "Detected Khoj" not in out


def test_config_init_claude_code_detection_when_dot_claude_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Setup ceremony step 5: `~/.claude/` triggers the bridge install
    (PR #19). Picker accepts all, bridge prompt says y, CLAUDE.md
    wiring prompt says n — we get a repos-index file but no
    CLAUDE.md touch."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    _mkrepo(fake_home / "Projects" / "thing")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    # Picker accepts all, install-bridge says y, with-claude-md says n.
    result = runner.invoke(
        app,
        [
            "config",
            "--init",
            "--skip-khoj-detect",
        ],
        input="all\ny\nn\n",
    )
    assert result.exit_code == 0, result.stdout

    out = _strip_ansi(result.stdout)
    assert "Found Claude Code" in out
    # PR #19: the bridge actually gets written.
    bridge = fake_home / ".claude" / "armillary" / "repos-index.md"
    assert bridge.exists()
    assert "thing" in bridge.read_text()
    # CLAUDE.md was NOT touched (user said no).
    assert not (fake_home / ".claude" / "CLAUDE.md").exists()


def test_config_init_claude_code_detection_skipped_when_no_dot_claude(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without `~/.claude/`, the Claude detection step is silent."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _mkrepo(fake_home / "Projects" / "thing")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(
        app,
        [
            "config",
            "--init",
            "--non-interactive",
            "--skip-khoj-detect",
        ],
    )
    assert result.exit_code == 0, result.stdout

    out = _strip_ansi(result.stdout)
    assert "Found Claude Code" not in out


def test_config_init_blank_does_not_run_setup_ceremony(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--init --blank` writes the placeholder and exits — no scan, no
    detection, no summary, nothing in cache."""
    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(app, ["config", "--init", "--blank"])
    assert result.exit_code == 0, result.stdout

    out = _strip_ansi(result.stdout)
    assert "Running initial scan" not in out
    assert "Indexed" not in out
    assert "Detected Khoj" not in out
    assert "Found Claude Code" not in out

    # Cache must be empty too
    with Cache() as cache:
        assert cache.count() == 0


def test_config_init_clears_stale_cache_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression for Codex review on PR #16: re-running init after
    removing the old config must NOT leave stale projects from the
    previous umbrella selection in the cache.

    `prune_stale()` only deletes rows older than 7 days; we need to
    actively wipe the cache before the new scan, otherwise recent
    rows from a removed umbrella linger in `armillary list` and the
    dashboard for up to a week.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    # Pre-populate the cache with a project from a "previous" umbrella
    # that is NOT going to be in the new config.
    from datetime import datetime as _dt

    from armillary.models import Project, ProjectType

    obsolete_dir = fake_home / "old-umbrella"
    obsolete_dir.mkdir(parents=True)
    obsolete = Project(
        path=obsolete_dir / "obsolete-project",
        name="obsolete-project",
        type=ProjectType.GIT,
        umbrella=obsolete_dir,
        last_modified=_dt.now(),
    )
    with Cache() as cache:
        cache.upsert([obsolete])
        assert cache.count() == 1

    # New umbrella, completely different
    new_umbrella = fake_home / "Projects"
    _mkrepo(new_umbrella / "fresh-project")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(
        app,
        [
            "config",
            "--init",
            "--non-interactive",
            "--skip-khoj-detect",
            "--skip-claude-detect",
        ],
    )
    assert result.exit_code == 0, result.stdout

    # The obsolete project must be GONE; only fresh-project remains.
    with Cache() as cache:
        names = {p.name for p in cache.list_projects()}
    assert names == {"fresh-project"}, (
        f"stale rows from previous setup leaked into post-init cache: {names}"
    )


def test_config_init_failed_initial_scan_does_not_abort_init(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Setup ceremony robustness: a crash inside the initial scan must
    NOT abort init. The YAML is already written and the user is in a
    recoverable state — print a warning and keep going."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _mkrepo(fake_home / "Projects" / "thing")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    # Make metadata.extract_all blow up on every call
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated GitPython explosion")

    from armillary import metadata as metadata_mod

    monkeypatch.setattr(metadata_mod, "extract_all", boom)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(
        app,
        [
            "config",
            "--init",
            "--non-interactive",
            "--skip-khoj-detect",
            "--skip-claude-detect",
        ],
    )
    # Init must NOT abort on scan failure
    assert result.exit_code == 0, result.stdout
    assert config_file.exists()

    out = _strip_ansi(result.stdout)
    assert "Initial scan failed" in out
    assert "simulated GitPython explosion" in out


def test_config_init_no_candidates_falls_back_to_blank(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Empty `~/` → bootstrap finds nothing → write blank placeholder."""
    fake_home = tmp_path / "empty-home"
    fake_home.mkdir()

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))
    monkeypatch.setenv("EDITOR", "true")

    result = runner.invoke(app, ["config", "--init"])
    assert result.exit_code == 0, result.stdout
    assert config_file.exists()
    # Falls back to the blank placeholder which uses ~/Projects.
    assert "~/Projects" in config_file.read_text()


def test_config_missing_file_without_init_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ARMILLARY_CONFIG", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("EDITOR", "true")

    result = runner.invoke(app, ["config"])
    assert result.exit_code != 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "does not exist" in combined.lower()


def test_config_opens_in_editor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text("umbrellas: []\n")
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))
    monkeypatch.setenv("EDITOR", "true")  # `true` exits 0 immediately

    captured: dict[str, Any] = {}

    real_run = cli_config.subprocess.run

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd
        return real_run(["true"], **kwargs)

    monkeypatch.setattr(cli_config.subprocess, "run", fake_run)

    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0, result.stdout
    assert captured["cmd"][0] == "true"
    assert str(config_file) in captured["cmd"][1]


def test_config_missing_editor_errors_clearly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text("umbrellas: []\n")
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))
    monkeypatch.setenv("EDITOR", "no-such-editor-program-zzz")

    result = runner.invoke(app, ["config"])
    assert result.exit_code != 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "editor" in combined.lower()


def test_config_supports_editor_with_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression for Codex review P3: `EDITOR='code --wait'` is a
    common shell setting and `armillary config` must split it before
    looking up the executable on PATH.
    """
    config_file = tmp_path / "config.yaml"
    config_file.write_text("umbrellas: []\n")
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))
    # `true` exists on PATH; pretend it takes arguments.
    monkeypatch.setenv("EDITOR", "true --wait --reuse-window")

    captured: dict[str, Any] = {}

    real_run = cli_config.subprocess.run

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd
        return real_run(["true"], **kwargs)

    monkeypatch.setattr(cli_config.subprocess, "run", fake_run)

    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0, result.stdout
    # Editor argv was parsed: ["true", "--wait", "--reuse-window", <config>]
    assert captured["cmd"][0] == "true"
    assert "--wait" in captured["cmd"]
    assert "--reuse-window" in captured["cmd"]
    assert str(config_file) in captured["cmd"][-1]


# --- M5: scan falls back to config umbrellas -------------------------------


def test_scan_uses_config_umbrellas_when_no_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    _mkrepo(workspace / "from-config")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"umbrellas:\n  - path: {workspace}\n    max_depth: 3\n")
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(app, ["scan"])  # no -u
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert any(p["name"] == "from-config" for p in data)


def test_scan_with_no_umbrellas_anywhere_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No -u and no config → friendly error, not a Python crash."""
    monkeypatch.setenv("ARMILLARY_CONFIG", str(tmp_path / "missing.yaml"))

    result = runner.invoke(app, ["scan"])
    assert result.exit_code != 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "no umbrellas" in combined.lower()


# --- M6: armillary search -------------------------------------------------


def test_search_empty_cache_prints_warning(tmp_path: Path) -> None:
    result = runner.invoke(app, ["search", "needle"])
    assert result.exit_code == 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "no projects in cache" in combined.lower()


def test_search_runs_ripgrep_against_cached_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: scan a tmp tree, then `armillary search needle` finds a hit."""
    import shutil as _shutil

    if _shutil.which("rg") is None:
        pytest.skip("ripgrep not installed")

    repo = tmp_path / "demo"
    _mkrepo(repo)
    (repo / "code.py").write_text("def needle():\n    return 1\n")

    runner.invoke(app, ["scan", "-u", str(tmp_path)])

    result = runner.invoke(app, ["search", "needle"])
    assert result.exit_code == 0, result.stdout
    out = _strip_ansi(result.stdout)
    assert "needle" in out
    assert "demo" in out


def test_search_with_project_filter_restricts_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil as _shutil

    if _shutil.which("rg") is None:
        pytest.skip("ripgrep not installed")

    target = tmp_path / "wanted"
    other = tmp_path / "ignored"
    _mkrepo(target)
    _mkrepo(other)
    (target / "x.py").write_text("uniqueneedle\n")
    (other / "y.py").write_text("uniqueneedle\n")

    runner.invoke(app, ["scan", "-u", str(tmp_path)])

    result = runner.invoke(app, ["search", "uniqueneedle", "--project", "wanted"])
    assert result.exit_code == 0, result.stdout
    out = _strip_ansi(result.stdout)
    assert "wanted" in out
    assert "ignored" not in out


def test_search_no_matches_prints_friendly_message(
    tmp_path: Path,
) -> None:
    import shutil as _shutil

    if _shutil.which("rg") is None:
        pytest.skip("ripgrep not installed")

    _mkrepo(tmp_path / "thing")
    runner.invoke(app, ["scan", "-u", str(tmp_path)])

    result = runner.invoke(app, ["search", "definitelynotinthisrepoxxxxx"])
    assert result.exit_code == 0
    out = _strip_ansi(result.stdout)
    assert "no matches" in out.lower()


# --- M7b / PR #19: armillary install-claude-bridge -------------------------


def test_install_claude_bridge_writes_to_fake_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`armillary install-claude-bridge` writes the repos-index to
    `~/.claude/armillary/repos-index.md`. Using a fake home so the test
    does not touch the developer's real `~/.claude`."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    # Populate the cache so the bridge has something to write.
    _mkrepo(tmp_path / "alpha")
    runner.invoke(app, ["scan", "-u", str(tmp_path)])

    result = runner.invoke(app, ["install-claude-bridge"])
    assert result.exit_code == 0, result.stdout

    bridge = fake_home / ".claude" / "armillary" / "repos-index.md"
    assert bridge.exists()
    assert "alpha" in bridge.read_text()
    # Without --with-claude-md, CLAUDE.md stays absent.
    assert not (fake_home / ".claude" / "CLAUDE.md").exists()


def test_install_claude_bridge_with_claude_md_appends_import_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--with-claude-md` wires the @armillary/repos-index.md line into
    CLAUDE.md. Re-running the command is a no-op (idempotent)."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    (fake_home / ".claude" / "CLAUDE.md").write_text("# existing rules\n")
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    _mkrepo(tmp_path / "thing")
    runner.invoke(app, ["scan", "-u", str(tmp_path)])

    result = runner.invoke(app, ["install-claude-bridge", "--with-claude-md"])
    assert result.exit_code == 0, result.stdout

    claude_md = fake_home / ".claude" / "CLAUDE.md"
    first_content = claude_md.read_text()
    assert "existing rules" in first_content
    assert "@armillary/repos-index.md" in first_content

    # Idempotent rerun
    result2 = runner.invoke(app, ["install-claude-bridge", "--with-claude-md"])
    assert result2.exit_code == 0
    second_content = claude_md.read_text()
    assert first_content == second_content
    out2 = _strip_ansi(result2.stdout)
    assert "already imports armillary" in out2


def test_install_claude_bridge_with_empty_cache_still_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No projects in cache: the command still writes an (empty) bridge
    file and warns the user to run a scan first."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    result = runner.invoke(app, ["install-claude-bridge"])
    assert result.exit_code == 0

    bridge = fake_home / ".claude" / "armillary" / "repos-index.md"
    assert bridge.exists()
    out = _strip_ansi(result.stdout)
    assert "cache is empty" in out.lower()


def test_scan_refresh_bridge_writes_after_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`armillary scan --refresh-bridge` refreshes the bridge repos-index
    after the scan completes."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    _mkrepo(tmp_path / "fresh")

    result = runner.invoke(app, ["scan", "-u", str(tmp_path), "--refresh-bridge"])
    assert result.exit_code == 0, result.stdout

    bridge = fake_home / ".claude" / "armillary" / "repos-index.md"
    assert bridge.exists()
    assert "fresh" in bridge.read_text()

    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "refresh-bridge" in combined


def test_scan_refresh_bridge_noop_without_claude_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without `~/.claude/`, `--refresh-bridge` is a warning, not an error."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()  # ~/.claude does NOT exist
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    _mkrepo(tmp_path / "solo")

    result = runner.invoke(app, ["scan", "-u", str(tmp_path), "--refresh-bridge"])
    assert result.exit_code == 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "not found" in combined.lower()
    assert not (fake_home / ".claude").exists()


def test_config_init_claude_bridge_with_claude_md_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Setup ceremony happy path: user says y to bridge install AND
    y to CLAUDE.md wiring. Both files get written."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    _mkrepo(fake_home / "Projects" / "alpha")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    # Picker: all. Install bridge: y. Wire CLAUDE.md: y.
    result = runner.invoke(
        app,
        ["config", "--init", "--skip-khoj-detect"],
        input="all\ny\ny\n",
    )
    assert result.exit_code == 0, result.stdout

    bridge = fake_home / ".claude" / "armillary" / "repos-index.md"
    claude_md = fake_home / ".claude" / "CLAUDE.md"
    assert bridge.exists()
    assert claude_md.exists()
    assert "@armillary/repos-index.md" in claude_md.read_text()


def test_scan_no_cache_plus_refresh_bridge_errors_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review P2: `--no-cache --refresh-bridge` would silently
    publish stale cache data. Reject the combo before scanning."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    _mkrepo(tmp_path / "thing")

    result = runner.invoke(
        app,
        ["scan", "-u", str(tmp_path), "--no-cache", "--refresh-bridge"],
    )
    assert result.exit_code == 2
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "refresh-bridge" in combined
    assert "no-cache" in combined


def test_config_init_skip_scan_does_not_publish_stale_bridge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review P2: init with `--skip-scan` must NOT install the
    Claude bridge from whatever old data the cache currently holds.
    The ceremony should skip the bridge step entirely (no y/n prompt)
    and point the user at the recovery command."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    _mkrepo(fake_home / "Projects" / "fresh")

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    # Prime the cache with a stale project from a previous hypothetical
    # init. This is what should NOT leak into the bridge.
    stale_dir = tmp_path / "stale_umbrella"
    _mkrepo(stale_dir / "ancient-project")
    runner.invoke(app, ["scan", "-u", str(stale_dir)])
    with Cache() as cache:
        assert any(p.name == "ancient-project" for p in cache.list_projects())

    # Re-init with --skip-scan. The ceremony must NOT prompt for bridge
    # install — it must see scan_succeeded=False and skip straight past.
    result = runner.invoke(
        app,
        [
            "config",
            "--init",
            "--skip-scan",
            "--skip-khoj-detect",
        ],
        input="all\n",
    )
    assert result.exit_code == 0, result.stdout

    bridge = fake_home / ".claude" / "armillary" / "repos-index.md"
    claude_md = fake_home / ".claude" / "CLAUDE.md"
    assert not bridge.exists(), "skip-scan + Claude detect must NOT publish the bridge"
    assert not claude_md.exists()

    out = _strip_ansi(result.stdout)
    assert "Found Claude Code" in out
    assert "install-claude-bridge" in out


def test_config_init_non_interactive_skips_bridge_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--non-interactive --init` with `~/.claude/` present must not
    install anything — respects the "no surprises in scripts" contract."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    _mkrepo(fake_home / "Projects" / "thing")

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    result = runner.invoke(
        app,
        ["config", "--init", "--non-interactive", "--skip-khoj-detect"],
    )
    assert result.exit_code == 0, result.stdout

    # Bridge must NOT have been installed — --non-interactive is opt-out.
    assert not (fake_home / ".claude" / "armillary" / "repos-index.md").exists()
    assert not (fake_home / ".claude" / "CLAUDE.md").exists()
    out = _strip_ansi(result.stdout)
    assert "install-claude-bridge" in out


# --- armillary install-khoj / start-khoj (Docker-based flow) --------------


class _FakeProc:
    """Bare minimum CompletedProcess stand-in for monkeypatched subprocess.run.

    Carries stdout/stderr so `capture_output=True` code paths that read
    them for user-facing error messages do not AttributeError."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _set_which(
    monkeypatch: pytest.MonkeyPatch,
    *,
    uv: str | None = "/fake/bin/uv",
    docker: str | None = "/fake/bin/docker",
) -> None:
    """Stub `cli_helpers.shutil_which` and `khoj_service.shutil_which` so
    install-khoj / start-khoj see (or miss) `uv` and `docker`
    deterministically regardless of the CI box."""
    real_which = cli_helpers.shutil_which

    def fake(name: str) -> str | None:
        if name == "uv":
            return uv
        if name == "docker":
            return docker
        return real_which(name)

    monkeypatch.setattr(cli_helpers, "shutil_which", fake)
    monkeypatch.setattr(cli_khoj, "shutil_which", fake)
    monkeypatch.setattr(khoj_service, "shutil_which", fake)


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kill `time.sleep` in the wait-for-postgres loop so tests fly."""
    monkeypatch.setattr(khoj_service.time, "sleep", lambda _s: None)


def test_install_khoj_provisions_docker_container_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full happy path: uv present, docker present, no existing khoj-pg
    container → install-khoj runs `uv pip install`, creates the
    container, waits for Postgres, enables pgvector, and points the
    user at `armillary start-khoj`."""
    _set_which(monkeypatch, uv="/fake/bin/uv", docker="/fake/bin/docker")
    _no_sleep(monkeypatch)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(cmd))
        # `docker ps -a --filter name=^khoj-pg$ --format {{.State}}` —
        # first call checks container state, we report "missing" by
        # returning empty stdout.
        if cmd[:2] == ["docker", "ps"]:
            return _FakeProc(returncode=0, stdout="")
        # Everything else (pip, docker run, pg_isready, docker exec)
        # succeeds.
        return _FakeProc(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli_khoj.subprocess, "run", fake_run)
    monkeypatch.setattr(khoj_service.subprocess, "run", fake_run)

    result = runner.invoke(app, ["install-khoj", "-y"])
    assert result.exit_code == 0, result.stdout

    # The expected call sequence: pip, ps-state, run, pg_isready, exec.
    # Assert pip install fired exactly once.
    assert any(c[:1] == ["/fake/bin/uv"] for c in calls)
    # Container state check
    assert any(c[:2] == ["docker", "ps"] and "--filter" in c for c in calls)
    # Container create
    docker_run = [c for c in calls if c[:2] == ["docker", "run"]]
    assert len(docker_run) == 1
    run_cmd = docker_run[0]
    assert "--name" in run_cmd
    assert "khoj-pg" in run_cmd
    assert "pgvector/pgvector:pg15" in run_cmd
    # pg_isready probe
    assert any("pg_isready" in c for c in calls)
    # CREATE EXTENSION
    exec_calls = [c for c in calls if c[:2] == ["docker", "exec"]]
    assert any(
        "CREATE EXTENSION IF NOT EXISTS vector;" in " ".join(c) for c in exec_calls
    )

    out = _strip_ansi(result.stdout)
    assert "armillary start-khoj" in out
    assert "armillary config --init --force" in out


def test_install_khoj_reuses_running_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `khoj-pg` is already running, install-khoj must NOT `docker
    run` again (that would fail with "name in use") — it just re-runs
    `CREATE EXTENSION IF NOT EXISTS vector` to make sure pgvector is on
    and prints the next-step hint."""
    _set_which(monkeypatch, uv="/fake/bin/uv", docker="/fake/bin/docker")
    _no_sleep(monkeypatch)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(cmd))
        if cmd[:2] == ["docker", "ps"]:
            return _FakeProc(returncode=0, stdout="running")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(cli_khoj.subprocess, "run", fake_run)
    monkeypatch.setattr(khoj_service.subprocess, "run", fake_run)

    result = runner.invoke(app, ["install-khoj", "-y"])
    assert result.exit_code == 0, result.stdout

    # No `docker run -d --name khoj-pg …` call — container already up.
    assert not any(c[:2] == ["docker", "run"] for c in calls)
    # But pgvector CREATE EXTENSION still ran.
    assert any(
        "CREATE EXTENSION IF NOT EXISTS vector;" in " ".join(c)
        for c in calls
        if c[:2] == ["docker", "exec"]
    )


def test_install_khoj_recreates_container_when_host_port_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real bug: older `install-khoj` runs created `khoj-pg` with
    host-port 5432, which collided with brew postgresql@14/@15 and
    silently routed psql traffic to the wrong Postgres (→
    "extension control file postgresql@14" crash). The new code
    expects host-port 54322; when it finds a container with a
    different host-side mapping it `docker rm -f`s and recreates.
    The named volume persists so embeddings survive."""
    _set_which(monkeypatch, uv="/fake/bin/uv", docker="/fake/bin/docker")
    _no_sleep(monkeypatch)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(cmd))
        # Container exists and is running on the OLD port
        if cmd[:2] == ["docker", "ps"]:
            return _FakeProc(returncode=0, stdout="running")
        # `docker port khoj-pg 5432/tcp` returns the old host mapping
        if cmd[:2] == ["docker", "port"]:
            return _FakeProc(returncode=0, stdout="0.0.0.0:5432\n")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(cli_khoj.subprocess, "run", fake_run)
    monkeypatch.setattr(khoj_service.subprocess, "run", fake_run)

    result = runner.invoke(app, ["install-khoj", "-y"])
    assert result.exit_code == 0, result.stdout

    # The stale container was force-removed …
    rm_calls = [c for c in calls if c[:3] == ["docker", "rm", "-f"]]
    assert len(rm_calls) == 1
    assert rm_calls[0][-1] == "khoj-pg"

    # … and a new one was docker-run with the NEW port mapping.
    run_calls = [c for c in calls if c[:2] == ["docker", "run"]]
    assert len(run_calls) == 1
    assert "54322:5432" in run_calls[0]

    out = _strip_ansi(result.stdout)
    assert "Recreating" in out or "recreating" in out
    assert "54322" in out


def test_install_khoj_starts_stopped_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `khoj-pg` exists but is stopped, install-khoj `docker start`s
    it rather than creating a duplicate."""
    _set_which(monkeypatch, uv="/fake/bin/uv", docker="/fake/bin/docker")
    _no_sleep(monkeypatch)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(cmd))
        if cmd[:2] == ["docker", "ps"]:
            return _FakeProc(returncode=0, stdout="exited")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(cli_khoj.subprocess, "run", fake_run)
    monkeypatch.setattr(khoj_service.subprocess, "run", fake_run)

    result = runner.invoke(app, ["install-khoj", "-y"])
    assert result.exit_code == 0, result.stdout
    assert any(c[:3] == ["docker", "start", "khoj-pg"] for c in calls)
    assert not any(c[:2] == ["docker", "run"] for c in calls)


def test_install_khoj_without_docker_exits_with_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker is now mandatory for install-khoj (we burned on brew
    pgvector/postgresql version skew). If docker is not on PATH, the
    command must fail with an actionable message pointing at Docker
    Desktop, NOT proceed to a broken provisioning attempt."""
    _set_which(monkeypatch, uv="/fake/bin/uv", docker=None)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(cmd))
        return _FakeProc(returncode=0)

    monkeypatch.setattr(cli_khoj.subprocess, "run", fake_run)
    monkeypatch.setattr(khoj_service.subprocess, "run", fake_run)

    result = runner.invoke(app, ["install-khoj", "-y"])
    assert result.exit_code != 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "Docker not found" in combined
    assert "docker.com" in combined
    # pip install still ran (that half works without docker)
    assert any(c[:1] == ["/fake/bin/uv"] for c in calls)
    # But no docker calls
    assert not any(c[:1] == ["docker"] for c in calls)


def test_install_khoj_aborts_without_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare Enter (default N) aborts without running anything."""
    _set_which(monkeypatch, uv="/fake/bin/uv", docker="/fake/bin/docker")
    _no_sleep(monkeypatch)

    called = False

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeProc:
        nonlocal called
        called = True
        return _FakeProc(returncode=0)

    monkeypatch.setattr(cli_khoj.subprocess, "run", fake_run)
    monkeypatch.setattr(khoj_service.subprocess, "run", fake_run)

    result = runner.invoke(app, ["install-khoj"], input="\n")
    assert result.exit_code != 0
    assert called is False
    out = _strip_ansi(result.stdout)
    assert "Aborted" in out


def test_install_khoj_bootstraps_pip_via_ensurepip_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real-world bug: `uv venv` without `--seed` produces a Python
    interpreter with no pip. The ensurepip retry path still works
    under the new docker-based flow — first pip install fails, we
    bootstrap pip, and retry succeeds. Then the docker provisioning
    runs normally."""
    _set_which(monkeypatch, uv=None, docker="/fake/bin/docker")
    _no_sleep(monkeypatch)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(cmd))
        # First pip install → fail. Ensurepip → OK. Retry pip → OK.
        # Everything else (docker) → OK.
        if cmd[:1] == [cli_khoj.sys.executable] and "pip" in cmd and "install" in cmd:
            pip_install_calls = [
                c
                for c in calls
                if c[:1] == [cli_khoj.sys.executable] and "pip" in c and "install" in c
            ]
            if len(pip_install_calls) == 1:
                return _FakeProc(returncode=1)
        if cmd[:2] == ["docker", "ps"]:
            return _FakeProc(returncode=0, stdout="")  # missing container
        return _FakeProc(returncode=0)

    monkeypatch.setattr(cli_khoj.subprocess, "run", fake_run)
    monkeypatch.setattr(khoj_service.subprocess, "run", fake_run)

    result = runner.invoke(app, ["install-khoj", "-y"])
    assert result.exit_code == 0, result.stdout
    # First pip, ensurepip, retry pip — all three fired before docker.
    pip_installs = [
        c for c in calls if "pip" in c and "install" in c and c[-1] == "khoj"
    ]
    assert len(pip_installs) == 2
    ensurepips = [c for c in calls if "ensurepip" in c]
    assert len(ensurepips) == 1


def test_install_khoj_surfaces_pip_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If pip install keeps failing (even after ensurepip), the CLI
    exits with the failing return code and prints workaround hints.
    Docker setup never runs — no point provisioning Postgres if the
    Python package did not install."""
    _set_which(monkeypatch, uv=None, docker="/fake/bin/docker")
    _no_sleep(monkeypatch)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(cmd))
        return _FakeProc(returncode=1)

    monkeypatch.setattr(cli_khoj.subprocess, "run", fake_run)
    monkeypatch.setattr(khoj_service.subprocess, "run", fake_run)

    result = runner.invoke(app, ["install-khoj", "-y"])
    assert result.exit_code == 1
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "Install failed with exit code 1" in combined
    assert "Common causes" in combined
    # Docker provisioning never fired
    assert not any(c[:2] == ["docker", "run"] for c in calls)


def test_install_khoj_errors_when_postgres_never_becomes_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `pg_isready` never answers 0 within the timeout, we must
    NOT proceed to CREATE EXTENSION (it would fail with a confusing
    connection error). Surface a clear "did not become ready" message
    pointing at `docker logs`."""
    _set_which(monkeypatch, uv="/fake/bin/uv", docker="/fake/bin/docker")
    _no_sleep(monkeypatch)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(cmd))
        if cmd[:2] == ["docker", "ps"]:
            return _FakeProc(returncode=0, stdout="running")
        if "pg_isready" in cmd:
            return _FakeProc(returncode=1)  # never ready
        return _FakeProc(returncode=0)

    monkeypatch.setattr(cli_khoj.subprocess, "run", fake_run)
    monkeypatch.setattr(khoj_service.subprocess, "run", fake_run)
    # Shrink the timeout so the test does not hang
    monkeypatch.setattr(khoj_service.time, "monotonic", iter([0.0, 1.0, 31.0]).__next__)

    result = runner.invoke(app, ["install-khoj", "-y"])
    assert result.exit_code != 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "did not become ready" in combined
    # CREATE EXTENSION must NOT have fired
    assert not any(
        "CREATE EXTENSION" in " ".join(c) for c in calls if c[:2] == ["docker", "exec"]
    )


# --- armillary start-khoj -------------------------------------------------


class _FakePopen:
    """Fake for subprocess.Popen that `start-khoj` uses.

    Records the command + kwargs, `poll()` returns 0 immediately
    (process "exited" cleanly), and `wait()` / `terminate()` are
    no-ops. This simulates a Khoj process that starts and exits
    normally, which is the happy path for the test — the health
    check fires before `proc.poll()` returns non-None.
    """

    def __init__(self, cmd: list[str], **kwargs: Any) -> None:
        self.cmd = list(cmd)
        self.kwargs = dict(kwargs)
        self.returncode = 0
        self._poll_count = 0

    def poll(self) -> int | None:
        self._poll_count += 1
        # First poll: "still running" so health check gets a chance.
        if self._poll_count <= 1:
            return None
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


def _setup_start_khoj_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    pre_seed_admin: dict[str, str] | None = None,
) -> tuple[Path, list[_FakePopen]]:
    """Shared setup for start-khoj tests.

    Returns (fake_khoj_path, popen_captures) so tests can assert
    on the captured Popen calls + env vars.
    """
    _set_which(monkeypatch, docker="/fake/bin/docker")
    _no_sleep(monkeypatch)

    monkeypatch.setenv("ARMILLARY_CONFIG", str(tmp_path / "armillary" / "config.yaml"))

    if pre_seed_admin:
        admin_dir = tmp_path / "armillary"
        admin_dir.mkdir(parents=True, exist_ok=True)
        (admin_dir / "khoj-admin.env").write_text(
            "\n".join(f"{k}={v}" for k, v in pre_seed_admin.items()) + "\n"
        )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_khoj = fake_bin / "khoj"
    fake_khoj.write_text("#!/bin/sh\necho fake khoj\n")
    fake_khoj.chmod(0o755)
    fake_python = fake_bin / "python"
    fake_python.write_text("")
    monkeypatch.setattr(cli_khoj.sys, "executable", str(fake_python))
    monkeypatch.setattr(khoj_service.sys, "executable", str(fake_python))

    # Mock subprocess.run (docker calls via khoj_service) +
    # subprocess.Popen (khoj binary launch via cli_khoj).
    popen_captures: list[_FakePopen] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeProc:
        if cmd[:2] == ["docker", "ps"]:
            return _FakeProc(returncode=0, stdout="running")
        return _FakeProc(returncode=0)

    def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
        fp = _FakePopen(cmd, **kwargs)
        popen_captures.append(fp)
        return fp

    monkeypatch.setattr(cli_khoj.subprocess, "run", fake_run)
    monkeypatch.setattr(khoj_service.subprocess, "run", fake_run)
    monkeypatch.setattr(cli_khoj.subprocess, "Popen", fake_popen)

    # Mock the health-check urlopen inside _wait_for_khoj_or_die.
    # It does `from urllib.request import urlopen as _urlopen` locally,
    # so we patch the module.
    import urllib.request

    class _FakeHealthResponse:
        status = 200

        def __enter__(self) -> _FakeHealthResponse:
            return self

        def __exit__(self, *exc: Any) -> None:
            pass

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **kw: _FakeHealthResponse(),
    )

    return fake_khoj, popen_captures


def test_start_khoj_execs_khoj_binary_with_env_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Happy path: container is running, khoj binary exists, we exec
    it with both POSTGRES_* and KHOJ_ADMIN_* env vars. The latter
    prevents Khoj from dropping into an interactive Email/Password
    prompt on first run (real bug — user hit this end-to-end)."""
    fake_khoj, popen_captures = _setup_start_khoj_env(monkeypatch, tmp_path)

    result = runner.invoke(app, ["start-khoj"])
    assert result.exit_code == 0, result.stdout

    assert len(popen_captures) == 1
    p = popen_captures[0]
    assert p.cmd == [str(fake_khoj), "--anonymous-mode", "--non-interactive"]

    env = p.kwargs.get("env") or {}
    assert env.get("POSTGRES_HOST") == "localhost"
    assert env.get("POSTGRES_PORT") == "54322"
    assert env.get("POSTGRES_DB") == "khoj"
    assert env.get("POSTGRES_USER") == "postgres"
    assert env.get("POSTGRES_PASSWORD") == "postgres"
    assert env.get("KHOJ_ADMIN_EMAIL") == "admin@armillary.local"
    assert env.get("KHOJ_ADMIN_PASSWORD")
    assert len(env["KHOJ_ADMIN_PASSWORD"]) >= 16
    assert env.get("KHOJ_TELEMETRY_DISABLE") == "true"

    admin_env_path = tmp_path / "armillary" / "khoj-admin.env"
    assert admin_env_path.is_file()
    file_contents = admin_env_path.read_text()
    assert "KHOJ_ADMIN_EMAIL=admin@armillary.local" in file_contents
    assert f"KHOJ_ADMIN_PASSWORD={env['KHOJ_ADMIN_PASSWORD']}" in file_contents


def test_start_khoj_reuses_existing_admin_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Second run must NOT re-roll the password — otherwise the admin
    account persisted in Postgres would drift from the env and Khoj
    would hit "authentication failed" on every subsequent restart."""
    fake_khoj, popen_captures = _setup_start_khoj_env(
        monkeypatch,
        tmp_path,
        pre_seed_admin={
            "KHOJ_ADMIN_EMAIL": "existing@localhost",
            "KHOJ_ADMIN_PASSWORD": "sticky-password-12345",
        },
    )

    result = runner.invoke(app, ["start-khoj"])
    assert result.exit_code == 0, result.stdout

    assert len(popen_captures) == 1
    env = popen_captures[0].kwargs.get("env") or {}
    assert env.get("KHOJ_ADMIN_EMAIL") == "existing@localhost"
    assert env.get("KHOJ_ADMIN_PASSWORD") == "sticky-password-12345"


def test_start_khoj_without_docker_errors_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_which(monkeypatch, docker=None)

    result = runner.invoke(app, ["start-khoj"])
    assert result.exit_code != 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "Docker not found" in combined


def test_start_khoj_without_container_tells_user_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing container → tell user to run `install-khoj` first,
    do NOT silently try to create one."""
    _set_which(monkeypatch, docker="/fake/bin/docker")

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeProc:
        if cmd[:2] == ["docker", "ps"]:
            return _FakeProc(returncode=0, stdout="")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(cli_khoj.subprocess, "run", fake_run)
    monkeypatch.setattr(khoj_service.subprocess, "run", fake_run)

    result = runner.invoke(app, ["start-khoj"])
    assert result.exit_code != 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "does not exist" in combined
    assert "install-khoj" in combined


def test_start_khoj_restarts_stopped_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stopped container is an acceptable state — start-khoj should
    `docker start` it and continue."""
    fake_khoj, popen_captures = _setup_start_khoj_env(monkeypatch, tmp_path)

    # Override fake_run to simulate stopped→running state transition
    state_calls = iter(["exited", "running"])
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeProc:
        calls.append(list(cmd))
        if cmd[:2] == ["docker", "ps"]:
            try:
                return _FakeProc(returncode=0, stdout=next(state_calls))
            except StopIteration:
                return _FakeProc(returncode=0, stdout="running")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(cli_khoj.subprocess, "run", fake_run)
    monkeypatch.setattr(khoj_service.subprocess, "run", fake_run)

    result = runner.invoke(app, ["start-khoj"])
    assert result.exit_code == 0, result.stdout
    assert any(c[:3] == ["docker", "start", "khoj-pg"] for c in calls)


def test_start_khoj_errors_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """khoj binary not in venv and not on PATH → clear error pointing
    at `install-khoj`."""
    _set_which(monkeypatch, docker="/fake/bin/docker")
    _no_sleep(monkeypatch)

    # Fake python in a dir that has NO khoj sibling
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    fake_python.write_text("")
    monkeypatch.setattr(cli_khoj.sys, "executable", str(fake_python))
    monkeypatch.setattr(khoj_service.sys, "executable", str(fake_python))

    # Also pretend `khoj` is not on PATH
    real_which = cli_helpers.shutil_which

    def no_khoj(name: str) -> str | None:
        if name == "khoj":
            return None
        if name == "docker":
            return "/fake/bin/docker"
        return real_which(name)

    monkeypatch.setattr(cli_helpers, "shutil_which", no_khoj)
    monkeypatch.setattr(cli_khoj, "shutil_which", no_khoj)
    monkeypatch.setattr(khoj_service, "shutil_which", no_khoj)

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeProc:
        if cmd[:2] == ["docker", "ps"]:
            return _FakeProc(returncode=0, stdout="running")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(cli_khoj.subprocess, "run", fake_run)
    monkeypatch.setattr(khoj_service.subprocess, "run", fake_run)

    result = runner.invoke(app, ["start-khoj"])
    assert result.exit_code != 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "khoj" in combined.lower()
    assert "install-khoj" in combined


def test_search_khoj_disabled_errors_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--khoj` without `khoj.enabled: true` should not silently fall through."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("khoj:\n  enabled: false\n")
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    _mkrepo(tmp_path / "thing")
    runner.invoke(app, ["scan", "-u", str(tmp_path)])

    result = runner.invoke(app, ["search", "anything", "--khoj"])
    assert result.exit_code != 0
    combined = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "khoj" in combined.lower()
    assert "not enabled" in combined.lower()


def test_scan_then_rescan_reflects_removed_projects_after_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a repo disappears and the cutoff has passed, prune_stale wipes it.

    Simulated by running two scans of different umbrella subsets and then
    forcing the older row's last_scanned_at into the past.
    """
    _mkrepo(tmp_path / "gone")
    _mkrepo(tmp_path / "kept")

    runner.invoke(app, ["scan", "-u", str(tmp_path)])

    # Backdate "gone" to look like it was last seen 30 days ago, then
    # remove it from the filesystem and rescan.
    import time as _time

    long_ago = _time.time() - 30 * 86400
    with Cache() as cache:
        cache.conn.execute(
            "UPDATE projects SET last_scanned_at = ? WHERE name = ?",
            (long_ago, "gone"),
        )
        cache.conn.commit()

    # Re-scan only the survivor; default prune_stale (7-day cutoff) drops
    # the stale row.
    import shutil as _shutil

    _shutil.rmtree(tmp_path / "gone")
    runner.invoke(app, ["scan", "-u", str(tmp_path)])

    with Cache() as cache:
        names = {p.name for p in cache.list_projects()}
    assert names == {"kept"}

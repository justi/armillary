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

from armillary import cli
from armillary.cache import Cache
from armillary.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_cache(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redirect the SQLite cache to a per-test tmp location.

    Without this, every `armillary scan` invocation in test_cli.py would
    write into the user's real `~/Library/Application Support/armillary/
    cache.db`. Autouse so individual tests cannot forget.
    """
    db_path = tmp_path_factory.mktemp("armi-cache") / "cache.db"
    monkeypatch.setenv("ARMILLARY_CACHE_DB", str(db_path))


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


@pytest.mark.parametrize(
    "command, milestone",
    [
        (["search", "needle"], "M4"),
    ],
)
def test_placeholder_commands_exit_zero_with_notice(
    command: list[str], milestone: str
) -> None:
    result = runner.invoke(app, command)
    assert result.exit_code == 0
    assert "not implemented" in result.stdout
    assert milestone.lower() in result.stdout.lower()


# --- M3.1: scan persists to cache, list reads back -------------------------


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


def test_config_init_creates_starter_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "armillary" / "config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(config_file))

    # `--init` writes the starter file then immediately tries to open
    # `$EDITOR`. Use `true` as a no-op editor for the test.
    monkeypatch.setenv("EDITOR", "true")

    result = runner.invoke(app, ["config", "--init"])
    assert result.exit_code == 0, result.stdout
    assert config_file.exists()
    assert "umbrellas" in config_file.read_text()


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

    real_run = cli.subprocess.run

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd
        return real_run(["true"], **kwargs)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

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

"""CLI surface tests — Typer's CliRunner against `armillary.cli.app`.

These tests cover argument parsing, JSON shape, exit codes, and the
subprocess call for `start`. The underlying scanner logic is covered
by `test_scanner.py`; here we only verify the CLI wiring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from armillary import cli
from armillary.cli import app

runner = CliRunner()


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
    assert item["metadata"] is None
    # last_modified must be ISO-8601 parseable
    from datetime import datetime

    datetime.fromisoformat(item["last_modified"])


def test_scan_accepts_multiple_umbrellas(tmp_path: Path) -> None:
    a = tmp_path / "A"
    b = tmp_path / "B"
    _mkrepo(a / "one")
    _mkidea(b / "two")

    result = runner.invoke(
        app, ["scan", "-u", str(a), "-u", str(b)]
    )

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

    shallow = runner.invoke(
        app, ["scan", "-u", str(tmp_path), "--max-depth", "2"]
    )
    deeper = runner.invoke(
        app, ["scan", "-u", str(tmp_path), "--max-depth", "3"]
    )

    assert shallow.exit_code == 0 and deeper.exit_code == 0
    assert json.loads(shallow.stdout) == []
    assert [item["name"] for item in json.loads(deeper.stdout)] == ["repo"]


def test_scan_requires_umbrella_flag() -> None:
    result = runner.invoke(app, ["scan"])

    assert result.exit_code != 0
    # Typer/Click reports missing required option
    assert "umbrella" in (result.stdout + result.stderr).lower()


def test_scan_short_flags_match_long(tmp_path: Path) -> None:
    _mkrepo(tmp_path / "r")

    long = runner.invoke(
        app, ["scan", "--umbrella", str(tmp_path), "--max-depth", "3"]
    )
    short = runner.invoke(app, ["scan", "-u", str(tmp_path), "-d", "3"])

    assert long.exit_code == 0 and short.exit_code == 0
    assert json.loads(long.stdout) == json.loads(short.stdout)


# --- regression: P3 (--max-depth bounds enforced at CLI boundary) ----------


@pytest.mark.parametrize("bad_value", ["0", "11", "-1", "999"])
def test_scan_rejects_max_depth_out_of_range(
    tmp_path: Path, bad_value: str
) -> None:
    """Out-of-range --max-depth must produce a clean Click usage error,
    not a Pydantic ValidationError traceback from inside the command body.
    """
    result = runner.invoke(
        app, ["scan", "-u", str(tmp_path), "--max-depth", bad_value]
    )

    assert result.exit_code != 0
    combined = result.stdout + (result.stderr or "")
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


def test_start_invokes_streamlit_with_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
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
    combined = result.stdout + (result.stderr or "")
    assert "streamlit" in combined.lower()
    assert "not installed" in combined.lower()


# --- placeholder commands (M3-M5) -------------------------------------------


@pytest.mark.parametrize(
    "command, milestone",
    [
        (["list"], "M3"),
        (["search", "needle"], "M4"),
        (["open", "some-project"], "M5"),
        (["config"], "M5"),
    ],
)
def test_placeholder_commands_exit_zero_with_notice(
    command: list[str], milestone: str
) -> None:
    result = runner.invoke(app, command)
    assert result.exit_code == 0
    assert "not implemented" in result.stdout
    assert milestone.lower() in result.stdout.lower()

"""Tests for the YAML config loader.

Each test writes a fresh `config.yaml` into `tmp_path` and points the
loader at it explicitly. We do not need an autouse fixture here because
`load_config` accepts an explicit path argument.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from armillary.config import (
    Config,
    ConfigError,
    UmbrellaConfig,
    default_config_path,
    load_config,
)

# --- default_config_path --------------------------------------------------


def test_default_config_path_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARMILLARY_CONFIG", "/tmp/custom-armillary-config.yaml")
    assert default_config_path() == Path("/tmp/custom-armillary-config.yaml")


def test_default_config_path_expands_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARMILLARY_CONFIG", "~/foo/bar.yaml")
    result = default_config_path()
    assert "~" not in str(result)
    assert result.is_absolute()


def test_default_config_path_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARMILLARY_CONFIG", raising=False)
    result = default_config_path()
    assert result.name == "config.yaml"
    assert "armillary" in result.parts


# --- load_config: missing / empty -----------------------------------------


def test_load_config_missing_file_returns_empty_config(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nope.yaml")
    assert isinstance(cfg, Config)
    assert cfg.umbrellas == []
    # Built-in launchers are always present even with no config file.
    assert "claude-code" in cfg.launchers
    assert "vscode" in cfg.launchers


def test_load_config_empty_file_returns_empty_config(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("")
    cfg = load_config(cfg_file)
    assert cfg.umbrellas == []
    assert cfg.launchers["claude-code"].command == "claude"


# --- load_config: umbrellas ----------------------------------------------


def test_load_config_parses_umbrellas(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "umbrellas:\n"
        "  - path: /tmp/work\n"
        "    label: Work\n"
        "    max_depth: 4\n"
        "  - path: /tmp/play\n"
    )
    cfg = load_config(cfg_file)
    assert len(cfg.umbrellas) == 2
    assert cfg.umbrellas[0].path == Path("/tmp/work")
    assert cfg.umbrellas[0].label == "Work"
    assert cfg.umbrellas[0].max_depth == 4
    assert cfg.umbrellas[1].label is None
    assert cfg.umbrellas[1].max_depth == 3  # default


def test_load_config_rejects_invalid_max_depth(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("umbrellas:\n  - path: /tmp\n    max_depth: 99\n")
    with pytest.raises(ConfigError):
        load_config(cfg_file)


# --- load_config: launchers ----------------------------------------------


def test_load_config_user_launchers_merge_with_builtins(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "launchers:\n"
        "  nvim:\n"
        "    label: Neovim\n"
        "    command: nvim\n"
        '    args: ["{path}"]\n'
    )
    cfg = load_config(cfg_file)
    # User launcher present
    assert "nvim" in cfg.launchers
    assert cfg.launchers["nvim"].command == "nvim"
    # Built-ins still there
    assert "claude-code" in cfg.launchers
    assert "vscode" in cfg.launchers


def test_load_config_user_can_override_builtin_launcher(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "launchers:\n"
        "  cursor:\n"
        "    label: Cursor (custom)\n"
        "    command: my-cursor-wrapper\n"
        '    args: ["--workspace", "{path}"]\n'
    )
    cfg = load_config(cfg_file)
    assert cfg.launchers["cursor"].command == "my-cursor-wrapper"
    assert cfg.launchers["cursor"].label == "Cursor (custom)"
    assert cfg.launchers["cursor"].args == ["--workspace", "{path}"]


def test_load_config_partial_override_keeps_builtin_fields(tmp_path: Path) -> None:
    """Regression for Codex review P2: a config that overrides only one
    field of a built-in launcher must inherit the rest, not wipe them."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("launchers:\n  cursor:\n    command: my-wrapper\n")
    cfg = load_config(cfg_file)
    assert cfg.launchers["cursor"].command == "my-wrapper"
    # `label`, `args`, `icon` come from the built-in entry
    assert cfg.launchers["cursor"].label == "Cursor"
    assert cfg.launchers["cursor"].args == ["{path}"]
    assert cfg.launchers["cursor"].icon == "📝"


def test_builtin_terminal_launchers_are_marked_terminal() -> None:
    """Codex / Claude Code are interactive terminal apps and must not
    be detached. Regression for Codex review P1.
    """
    builtins = Config.builtin_launchers()
    assert builtins["claude-code"].terminal is True
    assert builtins["codex"].terminal is True
    # GUI launchers stay non-terminal
    assert builtins["cursor"].terminal is False
    assert builtins["vscode"].terminal is False
    assert builtins["finder"].terminal is False


def test_builtin_codex_claude_dont_pass_path_as_positional() -> None:
    """Regression for Codex review P2: passing `{path}` to `codex` /
    `claude` makes them treat the project path as an initial prompt."""
    builtins = Config.builtin_launchers()
    assert builtins["claude-code"].args == []
    assert builtins["codex"].args == []


# --- load_config: errors --------------------------------------------------


def test_load_config_malformed_yaml_raises_friendly_error(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("umbrellas:\n  - this: [ unclosed")
    with pytest.raises(ConfigError, match="Could not parse"):
        load_config(cfg_file)


def test_load_config_unreadable_path_raises_friendly_error(tmp_path: Path) -> None:
    """Regression for Codex review P3: an unreadable path (e.g. a
    directory at the config location) must surface as ConfigError, not
    a Python traceback in `armillary scan` / `open`.
    """
    not_a_file = tmp_path / "config.yaml"
    not_a_file.mkdir()  # exists() is True but read_text raises IsADirectoryError
    with pytest.raises(ConfigError, match="Could not read"):
        load_config(not_a_file)


def test_load_config_root_must_be_mapping(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_config(cfg_file)


def test_load_config_ignores_unknown_top_level_keys(tmp_path: Path) -> None:
    """Forward compatibility: unknown keys at the root must not crash."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("future_feature: 42\numbrellas: []\n")
    cfg = load_config(cfg_file)
    assert cfg.umbrellas == []


# --- builtin_launchers ----------------------------------------------------


def test_builtin_launchers_returns_fresh_copy() -> None:
    a = Config.builtin_launchers()
    b = Config.builtin_launchers()
    assert a == b
    # Mutating one must not affect the other
    a.pop("vscode")
    assert "vscode" in b


def test_umbrella_config_validates_max_depth_at_construction() -> None:
    from pydantic import ValidationError

    UmbrellaConfig(path=Path("/tmp"), max_depth=1)
    UmbrellaConfig(path=Path("/tmp"), max_depth=10)
    with pytest.raises(ValidationError):
        UmbrellaConfig(path=Path("/tmp"), max_depth=0)
    with pytest.raises(ValidationError):
        UmbrellaConfig(path=Path("/tmp"), max_depth=11)


# --- write_config (PR #18) -------------------------------------------------


def test_write_config_round_trip(tmp_path: Path) -> None:
    """Build a fully-populated Config, write, reload, assert equal on
    every user-facing field."""
    from armillary.config import (
        KhojConfigBlock,
        LauncherConfig,
        write_config,
    )

    target = tmp_path / "config.yaml"
    cfg = Config(
        umbrellas=[
            UmbrellaConfig(path=Path("/tmp/work"), label="Work", max_depth=4),
            UmbrellaConfig(path=Path("/tmp/play")),
        ],
        launchers={
            "cursor": LauncherConfig(label="Cursor", command="cursor", args=["{path}"]),
            "nvim": LauncherConfig(
                label="Neovim",
                command="nvim",
                args=["{path}"],
                terminal=True,
            ),
        },
        khoj=KhojConfigBlock(
            enabled=True,
            api_url="http://localhost:42110",
            api_key="secret",
            timeout_seconds=10.0,
        ),
    )

    written = write_config(cfg, target)
    assert written == target
    assert target.exists()

    reloaded = load_config(target)

    # Umbrellas
    assert len(reloaded.umbrellas) == 2
    assert reloaded.umbrellas[0].path == Path("/tmp/work")
    assert reloaded.umbrellas[0].label == "Work"
    assert reloaded.umbrellas[0].max_depth == 4
    assert reloaded.umbrellas[1].path == Path("/tmp/play")
    assert reloaded.umbrellas[1].label is None
    assert reloaded.umbrellas[1].max_depth == 3

    # Launchers
    assert "cursor" in reloaded.launchers
    assert reloaded.launchers["cursor"].command == "cursor"
    assert reloaded.launchers["nvim"].terminal is True

    # Khoj
    assert reloaded.khoj.enabled is True
    assert reloaded.khoj.api_url == "http://localhost:42110"
    assert reloaded.khoj.api_key == "secret"
    assert reloaded.khoj.timeout_seconds == 10.0


def test_write_config_is_idempotent(tmp_path: Path) -> None:
    """Two writes of the same Config produce byte-identical files.

    Critical for the dashboard settings page: Streamlit reruns the
    script after every interaction, so the writer can fire many times.
    Non-deterministic output (random key order, timestamps) would make
    `git diff` of the config file noisy.
    """
    from armillary.config import write_config

    target = tmp_path / "config.yaml"
    cfg = Config(
        umbrellas=[UmbrellaConfig(path=Path("/tmp/x"), label="X")],
    )

    write_config(cfg, target)
    first = target.read_bytes()

    write_config(cfg, target)
    second = target.read_bytes()

    assert first == second


def test_write_config_includes_header_comment(tmp_path: Path) -> None:
    """The first line is a fixed comment so users editing the YAML by
    hand know what manages it."""
    from armillary.config import write_config

    target = tmp_path / "config.yaml"
    write_config(
        Config(umbrellas=[UmbrellaConfig(path=Path("/tmp/x"))]),
        target,
    )
    text = target.read_text()
    assert text.startswith("# armillary config")


def test_write_config_empty_sections_serialize_cleanly(tmp_path: Path) -> None:
    """Config() with default-everything (empty umbrellas, default launchers,
    Khoj disabled) round-trips without raising."""
    from armillary.config import write_config

    target = tmp_path / "config.yaml"
    cfg = Config()
    write_config(cfg, target)
    reloaded = load_config(target)
    assert reloaded.umbrellas == []
    assert "cursor" in reloaded.launchers  # built-in catalogue restored
    assert reloaded.khoj.enabled is False


def test_write_config_atomic_via_tmp_then_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `os.replace` never fires, the original file must be untouched.

    Simulates a crash mid-save by raising INSIDE the os.replace call —
    the .tmp file might be on disk but the real config is unchanged.
    """
    from armillary.config import write_config

    target = tmp_path / "config.yaml"
    target.write_text("# original — must not be lost\numbrellas: []\n")
    original = target.read_text()

    def crashing_replace(src: object, dst: object) -> None:
        raise RuntimeError("simulated crash before replace")

    import armillary.config as cfg_mod

    monkeypatch.setattr(cfg_mod.os, "replace", crashing_replace)

    cfg = Config(umbrellas=[UmbrellaConfig(path=Path("/tmp/new"))])
    with pytest.raises(RuntimeError, match="simulated crash"):
        write_config(cfg, target)

    # The original file is intact
    assert target.read_text() == original


def test_write_config_honors_default_path_via_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When called with `path=None`, write_config writes to the path
    returned by `default_config_path()`, which honors `ARMILLARY_CONFIG`."""
    from armillary.config import write_config

    custom = tmp_path / "armi" / "custom-config.yaml"
    monkeypatch.setenv("ARMILLARY_CONFIG", str(custom))

    cfg = Config(umbrellas=[UmbrellaConfig(path=Path("/tmp/x"))])
    written = write_config(cfg)

    assert written == custom
    assert custom.exists()
    assert "umbrellas" in custom.read_text()

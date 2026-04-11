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


# --- load_config: errors --------------------------------------------------


def test_load_config_malformed_yaml_raises_friendly_error(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("umbrellas:\n  - this: [ unclosed")
    with pytest.raises(ConfigError, match="Could not parse"):
        load_config(cfg_file)


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

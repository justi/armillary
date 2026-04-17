"""Tests for small shared utility helpers."""

from __future__ import annotations

from pathlib import Path

from armillary.utils import load_json_str_list


def test_load_json_str_list_filters_non_strings(tmp_path: Path) -> None:
    payload = '["ok", 123, null, true, "still-ok", {"x": 1}]'
    path = tmp_path / "mixed.json"
    path.write_text(payload, encoding="utf-8")

    assert load_json_str_list(path) == ["ok", "still-ok"]

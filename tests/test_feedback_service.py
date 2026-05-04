"""Tests for feedback_service — thumbs up/down on Steal results."""

from __future__ import annotations

from pathlib import Path

import pytest

from armillary.feedback_service import (
    no_results_count,
    record_no_results,
    record_vote,
    vote_counts,
)


@pytest.fixture
def isolated_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ARMILLARY_CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.setenv("ARMILLARY_CODE_INDEX_DB", str(tmp_path / "code_index.db"))
    return tmp_path


def test_empty_query_is_ignored(isolated_index: Path) -> None:
    record_vote("", "/x.py", 1, 1)
    assert vote_counts("") == {"up": 0, "down": 0}


def test_invalid_vote_value_ignored(isolated_index: Path) -> None:
    record_vote("stripe", "/x.py", 1, 7)
    assert vote_counts("stripe") == {"up": 0, "down": 0}


def test_record_and_count(isolated_index: Path) -> None:
    record_vote("stripe webhook", "/x.py", 1, 1)
    record_vote("stripe webhook", "/y.py", 1, 1)
    record_vote("stripe webhook", "/z.py", 1, -1)
    counts = vote_counts("stripe webhook")
    assert counts == {"up": 2, "down": 1}


def test_query_normalisation_case_insensitive(isolated_index: Path) -> None:
    record_vote("Stripe Webhook", "/x.py", 1, 1)
    # Different casing and surrounding whitespace hits the same bucket.
    assert vote_counts("  stripe webhook  ") == {"up": 1, "down": 0}


# ----- ADR 0031 — no-results signal ----------------------------------------


def test_no_results_count_starts_at_zero(isolated_index: Path) -> None:
    assert no_results_count("nothing here") == 0


def test_record_no_results_accumulates(isolated_index: Path) -> None:
    record_no_results("rails authn middleware")
    record_no_results("rails authn middleware")
    record_no_results("rails authn middleware")
    assert no_results_count("rails authn middleware") == 3


def test_no_results_query_normalised_like_votes(isolated_index: Path) -> None:
    record_no_results("Stripe Webhook")
    assert no_results_count("  stripe webhook  ") == 1


def test_empty_no_results_query_ignored(isolated_index: Path) -> None:
    record_no_results("")
    record_no_results("   ")
    assert no_results_count("") == 0

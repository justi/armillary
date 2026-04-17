"""Tests for pulse_service — format + history."""

from __future__ import annotations

from armillary.pulse_service import PulseEntry, WeeklyPulse, format_pulse


def test_format_pulse_quiet_week() -> None:
    pulse = WeeklyPulse()
    assert "Quiet week" in format_pulse(pulse)


def test_format_pulse_all_sections() -> None:
    pulse = WeeklyPulse(
        worked_on=[PulseEntry("🔨", "proj-a", "active · 50h")],
        went_dormant=[PulseEntry("💤", "proj-b", "went dormant")],
        aging_wip=[PulseEntry("⚠️", "proj-c", "2 uncommitted files")],
    )
    out = format_pulse(pulse)
    assert "Worked on:" in out
    assert "proj-a" in out
    assert "Went dormant:" in out
    assert "proj-b" in out
    assert "Uncommitted work:" in out
    assert "proj-c" in out


def test_format_pulse_omits_empty_sections() -> None:
    pulse = WeeklyPulse(
        worked_on=[PulseEntry("🔨", "proj-a", "active")],
    )
    out = format_pulse(pulse)
    assert "Worked on:" in out
    assert "Went dormant:" not in out
    assert "Uncommitted work:" not in out

from __future__ import annotations

import pytest

from nba_ml.lab_live_api import project_live_total


def test_live_projection_starts_from_pregame_prior() -> None:
    result = project_live_total(0, 0, 1, "12:00", 221.5)
    assert result["current_total"] == 0
    assert result["projected_total"] == 221.5
    assert result["live_weight"] == 0


def test_live_projection_blends_observed_scoring_with_prior() -> None:
    result = project_live_total(60, 55, 3, "06:00", 220.0)
    assert result["current_total"] == 115
    assert result["projected_total"] > 115
    assert result["projected_total"] < result["pace_projection"]
    assert 0 < result["live_weight"] < 0.85


def test_live_projection_at_end_of_regulation_equals_current_total() -> None:
    result = project_live_total(110, 108, 4, "00:00", 220.0)
    assert result["projected_total"] == 218.0
    assert result["remaining_minutes"] == 0


def test_live_projection_handles_overtime() -> None:
    result = project_live_total(120, 118, 5, "02:30", 220.0)
    assert result["elapsed_minutes"] == 50.5
    assert result["remaining_minutes"] == 2.5
    assert result["projected_total"] >= 238


@pytest.mark.parametrize("clock", ["12:61", "-1:00", "abc", "13:00"])
def test_live_projection_rejects_invalid_clock(clock: str) -> None:
    with pytest.raises(ValueError):
        project_live_total(40, 38, 2, clock, 220.0)



@pytest.mark.parametrize(("clock", "expected"), [
    ("4:33.0", "04:33"),
    ("04:33", "04:33"),
    ("PT4M33.00S", "04:33"),
    ("PT33S", "00:33"),
])
def test_live_projection_normalizes_espn_clock_formats(clock: str, expected: str) -> None:
    result = project_live_total(16, 11, 1, clock, 220.0)
    assert result["remaining_minutes"] == pytest.approx(12 - (12 * 60 - (4 * 60 + 33 if expected == "04:33" else 33)) / 60)
    assert result["projected_total"] >= 27


def test_live_projection_rejects_overtime_clock_above_five_minutes() -> None:
    with pytest.raises(ValueError):
        project_live_total(120, 118, 5, "06:00", 220.0)

"""Tests de la confianza y los niveles A-D de los props."""

from __future__ import annotations

import pytest

from nba_ml.models.player_props import prop_confidence


@pytest.mark.parametrize(
    ("projected_pra", "sigma", "grade"),
    [
        (100.0, 0.0, "A"),
        (100.0, 20.0, "A"),
        (100.0, 35.0, "B"),
        (100.0, 50.0, "C"),
        (100.0, 51.0, "D"),
        (10.0, 20.0, "D"),
        (0.0, 5.0, "D"),
    ],
)
def test_prop_confidence_grades(projected_pra, sigma, grade):
    confidence, actual = prop_confidence(projected_pra, sigma)

    assert actual == grade
    assert 0 <= confidence <= 1


def test_prop_confidence_matches_percentage_thresholds():
    confidence, grade = prop_confidence(100.0, 20.0)

    assert confidence == 0.8
    assert grade == "A"

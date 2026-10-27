"""Tests del motor matematico del Bet Builder."""

from __future__ import annotations

import pytest

from nba_ml.betting import (
    american_to_decimal,
    combine_independent,
    expected_value,
    fair_decimal_odds,
    handicap_side_probability,
    potential_return,
    team_total_side_probability,
    total_side_probability,
    validate_decimal_odds,
    validate_probability,
    winner_side_probability,
)


def test_combine_independent_multiplies_and_handles_empty():
    assert combine_independent([]) is None
    assert combine_independent([0.5, 0.5]) == pytest.approx(0.25)
    assert combine_independent([0.6, 0.7, 0.8]) == pytest.approx(0.336)


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), "no-es-probabilidad"])
def test_probability_outside_range_is_rejected(value):
    with pytest.raises(ValueError):
        validate_probability(value)


def test_american_to_decimal_valid_conversions():
    assert american_to_decimal(-110) == pytest.approx(1.9090909, rel=1e-5)
    assert american_to_decimal(-200) == pytest.approx(1.5)
    assert american_to_decimal(150) == pytest.approx(2.5)


@pytest.mark.parametrize("value", [0, 50, -99, float("nan")])
def test_invalid_american_odds_are_rejected(value):
    with pytest.raises(ValueError):
        american_to_decimal(value)


def test_decimal_odds_and_ev_validation():
    assert validate_decimal_odds(2.5) == 2.5
    assert expected_value(0.5, 2.0) == pytest.approx(0.0)
    assert expected_value(0.25, 4.0) == pytest.approx(0.0)
    assert potential_return(10, 3.5) == pytest.approx(35.0)
    with pytest.raises(ValueError):
        validate_decimal_odds(1.0)
    with pytest.raises(ValueError):
        validate_decimal_odds(0.9)


def test_fair_odds_and_zero_probability():
    assert fair_decimal_odds(0.5) == pytest.approx(2.0)
    assert fair_decimal_odds(0.0) is None


def test_total_and_winner_probabilities_use_empirical_residuals():
    artifact = {"residuals": [-2.0, 0.0, 2.0, 4.0]}
    assert total_side_probability(artifact, 220.0, 220.0, "OVER") == 0.5
    assert total_side_probability(artifact, 220.0, 220.0, "UNDER") == 0.5
    assert total_side_probability(artifact, 220.0, 218.0, "OVER") == 0.75
    assert total_side_probability(artifact, 220.0, 218.0, "UNDER") == 0.25
    assert winner_side_probability(0.6, "HOME") == 0.6
    assert winner_side_probability(0.6, "AWAY") == pytest.approx(0.4)
    assert handicap_side_probability(0.6, "HOME") == 0.6
    assert handicap_side_probability(0.6, "AWAY") == pytest.approx(0.4)


def test_team_total_uses_paired_total_and_margin_residuals():
    # Residuos de total y margen alineados: local = (total + margen) / 2.
    artifact = {
        "residuals": [-4.0, 0.0, 4.0, 8.0],
        "margin_residuals": [0.0, 0.0, 0.0, 0.0],
    }
    # Proyeccion local 110; residuos local = -2, 0, 2, 4.
    assert team_total_side_probability(
        artifact, 110.0, 100.0, 110.0, "OVER", "home"
    ) == 0.5
    assert team_total_side_probability(
        artifact, 110.0, 100.0, 108.0, "OVER", "home"
    ) == 0.75
    assert team_total_side_probability(
        artifact, 110.0, 100.0, 108.0, "UNDER", "home"
    ) == 0.25
    assert team_total_side_probability(
        artifact, 110.0, 100.0, 100.0, "OVER", "away"
    ) == 0.5

"""Pruebas de los cálculos descriptivos del análisis externo."""

from __future__ import annotations

import pandas as pd
import pytest

import app as app_module


def test_great_circle_distance_phoenix_denver_is_reasonable():
    distance = app_module._great_circle_km(
        app_module.NBA_ARENA_COORDINATES["PHX"],
        app_module.NBA_ARENA_COORDINATES["DEN"],
    )
    assert distance == pytest.approx(941, abs=20)


def test_external_travel_uses_previous_game_venue_not_team_city():
    schedule = pd.DataFrame([
        {
            "game_id": "0022600001",
            "game_date": pd.Timestamp("2026-11-10"),
            "game_time_et": "19:00",
            "status": 3,
            "home": "PHX",
            "away": "LAL",
        },
        {
            "game_id": "0022600002",
            "game_date": pd.Timestamp("2026-11-15"),
            "game_time_et": "20:00",
            "status": 1,
            "home": "DEN",
            "away": "PHX",
        },
    ])
    result = app_module._external_team_travel(
        schedule, "PHX", "DEN", pd.Timestamp("2026-11-15"), False
    )
    assert result["from_arena"] == "PHX"
    assert result["to_arena"] == "DEN"
    assert result["distance_km"] == pytest.approx(941, abs=20)


def test_external_home_split_uses_only_completed_home_games_before_match():
    schedule = pd.DataFrame([
        {
            "game_date": pd.Timestamp("2026-11-01"),
            "status": 3,
            "home": "DEN",
            "away": "PHX",
            "home_score": 120,
            "away_score": 110,
            "is_neutral": False,
        },
        {
            "game_date": pd.Timestamp("2026-11-05"),
            "status": 3,
            "home": "DEN",
            "away": "LAL",
            "home_score": 115,
            "away_score": 105,
            "is_neutral": False,
        },
        {
            "game_date": pd.Timestamp("2026-11-20"),
            "status": 3,
            "home": "DEN",
            "away": "BOS",
            "home_score": 90,
            "away_score": 100,
            "is_neutral": False,
        },
    ])
    result = app_module._external_team_split(
        schedule, "DEN", "home", pd.Timestamp("2026-11-15")
    )
    assert result["games"] == 2
    assert result["points_for"] == 117.5
    assert result["points_against"] == 107.5
    assert result["margin"] == 10

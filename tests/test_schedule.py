"""Tests del calendario: normalizacion, temporada actual y filtros."""

from __future__ import annotations

import pandas as pd

from nba_ml.data.schedule import (
    current_season,
    normalize_schedule,
    schedule_path,
    upcoming_games,
)


def _raw_schedule() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "gameId": ["0012600001", "0022600001", "0022600002", "0022600003"],
            "gameDate": [
                "10/03/2026 00:00:00",
                "10/21/2026 00:00:00",
                "10/22/2026 00:00:00",
                "01/01/0001 00:00:00",
            ],
            "gameDateEst": [
                "2026-10-03T00:00:00Z",
                "2026-10-21T00:00:00Z",
                "2026-10-22T00:00:00Z",
                "0001-01-01T00:00:00Z",
            ],
            "gameTimeEst": [
                "1900-01-01T19:00:00Z",
                "1900-01-01T19:30:00Z",
                "1900-01-01T20:00:00Z",
                None,
            ],
            "gameStatus": [3, 1, 3, 1],
            "gameStatusText": ["Final", "7:30 pm ET", "Final", "TBD"],
            "homeTeam_teamTricode": ["TOR", "BOS", "LAL", "NYK"],
            "awayTeam_teamTricode": ["MIA", "NYK", "GSW", "BKN"],
            "arenaName": ["Montreal", "TD Garden", "Crypto.com", "MSG"],
            "isNeutral": [True, False, False, False],
        }
    )


def test_normalize_filters_preseason_and_invalid_dates():
    frame = normalize_schedule(_raw_schedule())

    assert list(frame.columns) == [
        "game_id",
        "game_date",
        "game_time_et",
        "status",
        "status_text",
        "home",
        "away",
        "arena",
        "is_neutral",
    ]
    assert len(frame) == 2
    assert set(frame["game_id"]) == {"0022600001", "0022600002"}
    assert frame["game_date"].dt.date.tolist() == [
        pd.Timestamp("2026-10-21").date(),
        pd.Timestamp("2026-10-22").date(),
    ]
    assert frame["game_time_et"].tolist() == ["19:30", "20:00"]
    assert frame["home"].tolist() == ["BOS", "LAL"]


def test_upcoming_filters_by_date_and_status():
    frame = normalize_schedule(_raw_schedule())

    today = upcoming_games(frame, "2026-10-21", "2026-10-21")
    assert today["home"].tolist() == ["BOS"]

    with_final = upcoming_games(frame, "2026-10-21", "2026-10-22", only_scheduled=False)
    assert with_final["home"].tolist() == ["BOS", "LAL"]

    lal = upcoming_games(
        frame, "2026-10-21", "2026-10-22", only_scheduled=False, teams=["lal"]
    )
    assert lal["home"].tolist() == ["LAL"]


def test_current_season_and_cache_path():
    assert current_season("2026-10-21") == "2026-27"
    assert current_season("2026-09-01") == "2026-27"
    assert current_season("2026-06-15") == "2025-26"

    path = schedule_path("2026-27")
    assert path.name == "schedule_2026_27.csv"
    assert "raw" in path.parts

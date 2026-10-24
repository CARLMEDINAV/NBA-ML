"""Tests de las estadisticas de jugadores."""

from __future__ import annotations

import pandas as pd
import pytest

from nba_ml.data.players import normalize_player_stats, player_stats_path


def _raw_stats() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "PLAYER_ID": [203999, 1629029, 1],
            "PLAYER_NAME": ["Nikola Jokic", "Luka Doncic", ""],
            "TEAM_ID": [1610612743, 1610612747, 1],
            "TEAM_ABBREVIATION": ["den", "lal", "bos"],
            "GP": [65, 64, 0],
            "MIN": [34.8, 35.8, 0.0],
            "PTS": [27.7, 33.5, 0.0],
            "REB": [12.9, 7.7, 0.0],
            "AST": [10.7, 8.3, 0.0],
        }
    )


def test_normalize_computes_pra_and_cleans_rows():
    frame = normalize_player_stats(_raw_stats())

    assert len(frame) == 2
    assert frame["team"].tolist() == ["DEN", "LAL"]
    assert frame["player_name"].tolist() == ["Nikola Jokic", "Luka Doncic"]
    assert frame["pra"].round(1).tolist() == [51.3, 49.5]
    assert frame["gp"].tolist() == [65, 64]
    assert frame["player_id"].tolist() == [203999, 1629029]


def test_normalize_empty_frame():
    frame = normalize_player_stats(pd.DataFrame())

    assert frame.empty
    assert "pra" in frame.columns


def test_normalize_requires_core_columns():
    with pytest.raises(ValueError, match="Faltan columnas"):
        normalize_player_stats(pd.DataFrame({"PLAYER_ID": [1]}))


def test_load_player_stats_retries_after_timeout(monkeypatch, tmp_path):
    import nba_api.stats.endpoints.leaguedashplayerstats as endpoint_module

    import nba_ml.data.players as players_module

    calls = []

    class FakeStats:
        def __init__(self, season, per_mode_detailed, timeout):
            calls.append((season, per_mode_detailed, timeout))
            if len(calls) == 1:
                raise TimeoutError("timeout")

        def get_data_frames(self):
            return [_raw_stats()]

    monkeypatch.setattr(endpoint_module, "LeagueDashPlayerStats", FakeStats)
    monkeypatch.setattr(
        players_module,
        "player_stats_path",
        lambda season, per_mode: tmp_path / "players.csv",
    )
    monkeypatch.setattr(players_module.time, "sleep", lambda seconds: None)

    frame = players_module.load_player_stats(
        "2026-27", per_mode="PerGame", refresh=True, timeout=1, retries=2
    )

    assert len(calls) == 2
    assert len(frame) == 2
    assert (tmp_path / "players.csv").exists()


def test_player_stats_cache_path():
    assert (
        player_stats_path("2025-26", "PerGame").name
        == "player_stats_2025_26_pergame.csv"
    )
    assert (
        player_stats_path("2025-26", "Totals").name
        == "player_stats_2025_26_totals.csv"
    )
    with pytest.raises(ValueError):
        player_stats_path("2025-26", "Per36")

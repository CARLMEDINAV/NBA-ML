import pandas as pd

from nba_ml import prop_lab_api


def _sample_history():
    values = [18, 22, 20, 25, 20]
    return pd.DataFrame({
        "season": ["2026-27"] * 5,
        "player_id": [7] * 5,
        "player_name": ["Test Player"] * 5,
        "team": ["BOS"] * 5,
        "game_id": [str(i) for i in range(5)],
        "game_date": pd.date_range("2026-11-01", periods=5),
        "min": [30] * 5,
        "pts": values,
        "reb": [5, 6, 4, 7, 5],
        "ast": [4, 5, 6, 3, 4],
        "fg3m": [2, 3, 1, 4, 2],
        "pra": [27, 33, 30, 35, 29],
    })


def test_trend_counts_over_under_and_pushes(monkeypatch):
    monkeypatch.setattr(prop_lab_api, "_history", _sample_history)
    result = prop_lab_api.prop_lab_trends(
        market="PTS", season="2026-27", window=5, line=20,
        side="ALL", min_sample=1, team=None, q=None, limit=100,
    )
    player = result["players"][0]
    assert player["over"] == 2
    assert player["under"] == 1
    assert player["push"] == 2
    assert player["valid"] == 3
    assert player["sample"] == 5


def test_3pm_uses_fg3m_history(monkeypatch):
    monkeypatch.setattr(prop_lab_api, "_history", _sample_history)
    result = prop_lab_api.prop_lab_trends(
        market="3PM", season="2026-27", window=5, line=2,
        side="ALL", min_sample=1, team=None, q=None, limit=100,
    )
    player = result["players"][0]
    assert player["average"] == 2.4
    assert player["over"] == 2
    assert player["under"] == 1
    assert player["push"] == 2


def test_under_filter_uses_only_players_with_under_hits(monkeypatch):
    monkeypatch.setattr(prop_lab_api, "_history", _sample_history)
    result = prop_lab_api.prop_lab_trends(
        market="REB", season="2026-27", window=5, line=4,
        side="UNDER", min_sample=1, team=None, q=None, limit=100,
    )
    assert result["count"] == 0

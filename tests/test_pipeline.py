"""Tests del pipeline de datos: emparejado, features causales y estado actual."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nba_ml.config import META_COLUMNS
from nba_ml.data.build_dataset import (
    BASE_FEATURES,
    LEAGUE_WINDOWS,
    MODEL_FEATURES,
    add_team_features,
    build_feature_row,
    build_team_games,
    build_team_state,
    pair_games,
)
from nba_ml.models.predict import _state_as_of
from nba_ml.models.train import FEATURES as SELECTED_FEATURES
from nba_ml.models.train import split_by_season


def make_raw(
    n_games: int = 8,
    spacing: int = 2,
    neutral_extra: bool = False,
) -> pd.DataFrame:
    """Dos equipos (BOS local, LAL visitante) con estadisticas sinteticas."""
    rows = []
    start = pd.Timestamp("2026-01-01")
    for index in range(n_games):
        date = start + pd.Timedelta(days=spacing * index)
        game_id = f"002250{index:04d}"
        home_pts = 100 + index
        away_pts = 105 - index
        common = {
            "game_date": date,
            "season": "2025-26",
            "min": 240,
            "fgm": 40,
            "fga": 85,
            "fg3m": 12,
            "fg3a": 35,
            "ftm": 20,
            "fta": 22,
            "oreb": 9,
            "dreb": 32,
            "reb": 41,
            "ast": 25,
            "stl": 7,
            "blk": 5,
            "tov": 13,
            "pf": 20,
            "plus_minus": 0,
            "video_available": 1,
        }
        rows.append(
            {
                **common,
                "game_id": game_id,
                "team_id": 1,
                "team_abbreviation": "BOS",
                "team_name": "Boston Celtics",
                "matchup": "BOS vs. LAL",
                "wl": "W" if home_pts > away_pts else "L",
                "pts": home_pts,
                "plus_minus": home_pts - away_pts,
            }
        )
        rows.append(
            {
                **common,
                "game_id": game_id,
                "team_id": 2,
                "team_abbreviation": "LAL",
                "team_name": "Los Angeles Lakers",
                "matchup": "LAL @ BOS",
                "wl": "W" if away_pts > home_pts else "L",
                "pts": away_pts,
                "plus_minus": away_pts - home_pts,
            }
        )

    if neutral_extra:
        neutral_id = "0022599999"
        rows.append(
            {
                "game_id": neutral_id,
                "team_id": 1,
                "team_abbreviation": "BOS",
                "team_name": "Boston Celtics",
                "game_date": start + pd.Timedelta(days=30),
                "season": "2025-26",
                "matchup": "BOS @ LAL",
                "wl": "W",
                "min": 240,
                "pts": 110,
                "fga": 85,
                "fg3a": 35,
                "fta": 22,
                "oreb": 9,
                "dreb": 32,
                "tov": 13,
            }
        )
        rows.append(
            {
                "game_id": neutral_id,
                "team_id": 2,
                "team_abbreviation": "LAL",
                "team_name": "Los Angeles Lakers",
                "game_date": start + pd.Timedelta(days=30),
                "season": "2025-26",
                "matchup": "LAL @ BOS",
                "wl": "L",
                "min": 240,
                "pts": 100,
                "fga": 85,
                "fg3a": 35,
                "fta": 22,
                "oreb": 9,
                "dreb": 32,
                "tov": 13,
            }
        )

    return pd.DataFrame(rows)


def test_pair_games_builds_one_row_per_game():
    games = pair_games(make_raw(n_games=2))
    assert len(games) == 2
    assert set(games["home_team"]) == {"BOS"}
    assert set(games["away_team"]) == {"LAL"}
    assert games["total_points"].tolist() == [205, 205]
    assert games["home_margin"].tolist() == [-5, -3]


def test_pair_games_drops_neutral_site_games():
    games = pair_games(make_raw(n_games=2, neutral_extra=True))
    assert len(games) == 2
    assert "0022599999" not in set(games["game_id"])


def test_rolling_features_use_only_previous_games():
    featured = add_team_features(build_team_games(make_raw(n_games=8)))
    bos = featured[featured["team_id"] == 1].sort_values("game_date").reset_index(drop=True)

    # El sexto partido (indice 5) solo puede ver los puntos 100..104.
    assert bos.loc[5, "pts_roll5"] == pytest.approx(102.0)
    assert bos.loc[5, "pts_roll10"] == pytest.approx(102.0)
    assert bos.loc[4, "pts_roll5"] == pytest.approx(101.5)

    # Los tres primeros partidos no tienen historial suficiente.
    assert bos.loc[:2, "pts_roll5"].isna().all()


def test_current_and_future_results_do_not_leak_into_features():
    base = add_team_features(build_team_games(make_raw(n_games=8)))
    reference = base[base["team_id"] == 1].sort_values("game_date").iloc[5]
    reference_value = reference["pts_roll5"]

    raw = make_raw(n_games=8)
    raw.loc[(raw["team_id"] == 1) & (raw["game_id"] == "0022500005"), "pts"] = 200
    raw.loc[(raw["team_id"] == 1) & (raw["game_id"] == "0022500007"), "pts"] = 150

    changed = add_team_features(build_team_games(raw))
    changed_row = changed[changed["team_id"] == 1].sort_values("game_date").iloc[5]

    assert changed_row["pts_roll5"] == pytest.approx(reference_value)
    assert changed_row["game_total_roll5"] == pytest.approx(reference["game_total_roll5"])


def test_rest_days_and_back_to_back():
    tight = add_team_features(build_team_games(make_raw(n_games=4, spacing=1)))
    bos = tight[tight["team_id"] == 1].sort_values("game_date")
    assert bos["days_rest"].iloc[1:].tolist() == [1.0, 1.0, 1.0]
    assert bos["is_b2b"].iloc[1:].tolist() == [1, 1, 1]

    relaxed = add_team_features(build_team_games(make_raw(n_games=4, spacing=3)))
    bos = relaxed[relaxed["team_id"] == 1].sort_values("game_date")
    assert bos["days_rest"].iloc[1:].tolist() == [3.0, 3.0, 3.0]
    assert bos["is_b2b"].iloc[1:].tolist() == [0, 0, 0]


def test_team_state_describes_the_next_game():
    team_games = build_team_games(make_raw(n_games=8))
    state = build_team_state(team_games)
    bos = state[state["team_abbreviation"] == "BOS"].iloc[0]

    assert bos["pts_roll10"] == pytest.approx(np.mean([100, 101, 102, 103, 104, 105, 106, 107]))
    assert bos["days_rest"] == 3.0
    assert bos["season_game_no"] == 9

    next_day = _state_as_of(bos, pd.Timestamp(bos["last_game_date"]) + pd.Timedelta(days=1))
    assert next_day["days_rest"] == 1.0
    assert next_day["is_b2b"] == 1
    # Del 9 al 16 de enero: partidos el 9, 11, 13 y 15.
    assert next_day["games_last_7d"] == 4


def test_state_as_of_new_season_applies_carryover():
    state = build_team_state(build_team_games(make_raw(n_games=6)))
    bos = state[state["team_abbreviation"] == "BOS"].iloc[0].copy()
    bos["elo"] = 1600.0

    updated = _state_as_of(bos, pd.Timestamp("2026-10-22"))
    assert updated["elo"] == pytest.approx(1575.0)
    assert updated["season_game_no"] == 1
    assert updated["days_rest"] == 10.0


def test_feature_row_matches_model_contract():
    team_games = build_team_games(make_raw(n_games=10))
    state = build_team_state(team_games)
    state["elo"] = 1500.0
    for window in LEAGUE_WINDOWS:
        state[f"league_total_roll{window}"] = 222.0
    state["month"] = 1

    home = state[state["team_abbreviation"] == "BOS"].iloc[0]
    away = state[state["team_abbreviation"] == "LAL"].iloc[0]
    row = build_feature_row(home, away)

    assert list(row.columns) == list(MODEL_FEATURES)
    assert len(row) == 1
    assert not row.isna().any(axis=None)
    for column in BASE_FEATURES:
        assert f"{column}_home" in MODEL_FEATURES
        assert f"{column}_away" in MODEL_FEATURES


def test_meta_columns_are_never_features():
    assert set(META_COLUMNS).isdisjoint(set(MODEL_FEATURES))


def test_selected_features_exist_in_dataset():
    assert set(SELECTED_FEATURES).issubset(set(MODEL_FEATURES))
    assert len(SELECTED_FEATURES) == len(set(SELECTED_FEATURES))


def test_season_split_is_temporal():
    frame = pd.DataFrame(
        {
            "season": ["2023-24", "2024-25", "2025-26"],
            "game_date": pd.to_datetime(["2024-04-01", "2025-04-01", "2026-04-01"]),
            "game_id": ["a", "b", "c"],
        }
    )
    train, validation, test = split_by_season(frame)
    assert len(train) == 1 and len(validation) == 1 and len(test) == 1
    assert train["game_date"].max() < validation["game_date"].min()
    assert validation["game_date"].max() < test["game_date"].min()

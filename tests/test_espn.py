"""Tests de la fuente alternativa de ESPN usada por los workflows."""

from __future__ import annotations

from datetime import date

import pandas as pd

from nba_ml.data.espn import (
    BOXSCORE_COLUMNS,
    TEAM_GAME_COLUMNS,
    PlayerLookup,
    _boxscore_rows,
    _event_row,
    _team_game_rows,
    merge_schedule,
    normalize_name,
)


def test_normalize_name_ignores_accents_and_suffixes():
    assert normalize_name("Luka Dončić") == "luka doncic"
    assert normalize_name("Bobby Portis Jr.") == "bobby portis"
    assert normalize_name("Jaren Jackson III") == "jaren jackson"


def test_event_row_uses_eastern_date_and_nba_abbreviations():
    event = {
        "id": "401909868",
        "date": "2026-10-26T23:00Z",
        "status": {
            "type": {
                "state": "post",
                "completed": True,
                "shortDetail": "Final",
            }
        },
        "competitions": [
            {
                "venue": {"fullName": "Spectrum Center"},
                "neutralSite": False,
                "competitors": [
                    {
                        "homeAway": "home",
                        "score": "110",
                        "team": {"abbreviation": "CHA"},
                    },
                    {
                        "homeAway": "away",
                        "score": "103",
                        "team": {"abbreviation": "WSH"},
                    },
                ],
            }
        ],
    }
    row = _event_row(event)
    assert row is not None
    assert row["game_date"] == date(2026, 10, 26)
    assert row["home"] == "CHA"
    assert row["away"] == "WAS"
    assert row["status"] == 3
    assert row["total_points"] == 213.0


def test_merge_schedule_preserves_nba_game_id():
    existing = pd.DataFrame(
        [
            {
                "game_id": "0022600119",
                "game_date": "2026-10-26",
                "game_time_et": "19:00",
                "status": 1,
                "status_text": "Programado",
                "home": "CHA",
                "away": "WAS",
                "arena": "Spectrum Center",
                "is_neutral": False,
                "home_score": 0,
                "away_score": 0,
                "total_points": float("nan"),
            }
        ]
    )
    games = [
        {
            "event_id": "401909868",
            "game_date": date(2026, 10, 26),
            "game_time_et": "19:00",
            "status": 3,
            "status_text": "Final",
            "home": "CHA",
            "away": "WAS",
            "arena": "Spectrum Center",
            "is_neutral": False,
            "home_score": 110,
            "away_score": 103,
            "total_points": 213.0,
        }
    ]
    merged = merge_schedule(existing, games)
    assert len(merged) == 1
    assert merged.loc[0, "game_id"] == "0022600119"
    assert merged.loc[0, "status"] == 3
    assert merged.loc[0, "home_score"] == 110
    assert merged.loc[0, "total_points"] == 213.0


def test_boxscore_rows_parse_espn_stat_keys():
    event = {
        "id": "401909868",
        "_date": date(2026, 10, 26),
        "_home": "CHA",
        "_away": "WAS",
        "_home_score": 110,
        "_away_score": 103,
    }
    summary = {
        "boxscore": {
            "players": [
                {
                    "team": {"abbreviation": "WSH"},
                    "statistics": [
                        {
                            "keys": [
                                "minutes",
                                "points",
                                "fieldGoalsMade-fieldGoalsAttempted",
                                "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
                                "freeThrowsMade-freeThrowsAttempted",
                                "rebounds",
                                "assists",
                                "turnovers",
                                "steals",
                                "blocks",
                                "offensiveRebounds",
                                "defensiveRebounds",
                                "fouls",
                                "plusMinus",
                            ],
                            "athletes": [
                                {
                                    "athlete": {"displayName": "Anthony Davis"},
                                    "didNotPlay": False,
                                    "stats": [
                                        "36",
                                        "29",
                                        "11-22",
                                        "3-6",
                                        "4-4",
                                        "10",
                                        "0",
                                        "0",
                                        "1",
                                        "0",
                                        "0",
                                        "10",
                                        "2",
                                        "+6",
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    }
    lookup = PlayerLookup(
        by_name={"anthony davis": 203076},
        by_name_team={("anthony davis", "WAS"): 203076},
        names={203076: "Anthony Davis"},
    )
    rows = _boxscore_rows(
        summary,
        event,
        season="2026-27",
        game_id="0022600119",
        lookup=lookup,
        team_ids={},
    )
    assert len(rows) == 1
    row = rows[0]
    assert set(BOXSCORE_COLUMNS).issubset(row)
    assert row["player_id"] == 203076
    assert row["team"] == "WAS"
    assert row["opponent"] == "CHA"
    assert row["is_home"] is False
    assert row["wl"] == "L"
    assert row["pts"] == 29
    assert row["reb"] == 10
    assert row["fgm"] == 11
    assert row["fga"] == 22
    assert row["fg3m"] == 3
    assert row["fta"] == 4
    assert row["pra"] == 39


def test_team_game_rows_parse_espn_team_boxscore():
    event = {
        "id": "401909088",
        "_date": date(2026, 10, 20),
        "_home": "DET",
        "_away": "BOS",
        "_home_score": 121,
        "_away_score": 113,
        "_status_text": "Final/OT",
    }
    summary = {
        "header": {"competitions": [{"status": {"period": 5}}]},
        "boxscore": {
            "teams": [
                {
                    "team": {
                        "abbreviation": "BOS",
                        "displayName": "Boston Celtics",
                    },
                    "statistics": [
                        {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "42-88"},
                        {"name": "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "displayValue": "15-40"},
                        {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "14-20"},
                        {"name": "totalRebounds", "displayValue": "40"},
                        {"name": "offensiveRebounds", "displayValue": "8"},
                        {"name": "defensiveRebounds", "displayValue": "32"},
                        {"name": "assists", "displayValue": "25"},
                        {"name": "steals", "displayValue": "7"},
                        {"name": "blocks", "displayValue": "4"},
                        {"name": "turnovers", "displayValue": "16"},
                        {"name": "fouls", "displayValue": "21"},
                    ],
                },
                {
                    "team": {
                        "abbreviation": "DET",
                        "displayName": "Detroit Pistons",
                    },
                    "statistics": [
                        {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "44-90"},
                        {"name": "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "displayValue": "10-30"},
                        {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "23-30"},
                        {"name": "totalRebounds", "displayValue": "50"},
                        {"name": "offensiveRebounds", "displayValue": "12"},
                        {"name": "defensiveRebounds", "displayValue": "38"},
                        {"name": "assists", "displayValue": "28"},
                        {"name": "steals", "displayValue": "9"},
                        {"name": "blocks", "displayValue": "5"},
                        {"name": "turnovers", "displayValue": "14"},
                        {"name": "fouls", "displayValue": "20"},
                    ],
                },
            ]
        },
    }
    rows = _team_game_rows(
        summary,
        event,
        season="2026-27",
        game_id="0022600001",
        team_ids={"BOS": 1610612738, "DET": 1610612765},
    )
    assert len(rows) == 2
    assert set(TEAM_GAME_COLUMNS).issubset(rows[0])
    det = next(row for row in rows if row["team_abbreviation"] == "DET")
    assert det["team_name"] == "Detroit Pistons"
    assert det["pts"] == 121
    assert det["opp_pts"] == 113
    assert det["is_home"] is True
    assert det["opponent_id"] == 1610612738
    assert det["fga"] == 90
    assert det["oreb"] == 12
    assert det["ot_flag"] == 1.0
    assert det["close_flag"] == 0.0
    assert det["win"] == 1
    assert det["pace"] > 90

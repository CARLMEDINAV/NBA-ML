from nba_ml.live_player_api import _parse_player_stats


def test_parse_espn_player_boxscore_stats():
    summary = {
        "boxscore": {
            "players": [{
                "team": {"id": "2", "abbreviation": "BOS", "displayName": "Boston Celtics"},
                "statistics": [{
                    "names": ["MIN", "FG", "3PT", "FT", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TO", "PF", "+/-", "PTS"],
                    "athletes": [{
                        "athlete": {"id": "202331", "displayName": "Paul George"},
                        "stats": ["18:42", "6-11", "2-5", "3-4", "1", "4", "5", "3", "2", "1", "1", "2", "+7", "17"],
                    }],
                }],
            }],
        },
    }
    stats, team = _parse_player_stats(summary, "202331")
    assert team["abbreviation"] == "BOS"
    assert stats["min"] == "18:42"
    assert stats["pts"] == 17
    assert stats["reb"] == 5
    assert stats["ast"] == 3
    assert stats["fg3m"] == 2
    assert stats["stl"] == 2
    assert stats["blk"] == 1
    assert stats["tov"] == 1


def test_parse_espn_returns_none_when_player_not_in_boxscore():
    stats, team = _parse_player_stats({"boxscore": {"players": []}}, "202331")
    assert stats is None
    assert team is None



def test_parse_espn_player_boxscore_by_name_when_ids_differ():
    summary = {
        "boxscore": {
            "players": [{
                "team": {"id": "2", "abbreviation": "BOS", "displayName": "Boston Celtics"},
                "statistics": [{
                    "names": ["MIN", "FG", "3PT", "FT", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TO", "PF", "+/-", "PTS"],
                    "athletes": [{
                        "athlete": {"id": "4251", "displayName": "Paul George"},
                        "stats": ["18:42", "6-11", "2-5", "3-4", "1", "4", "5", "3", "2", "1", "1", "2", "+7", "17"],
                    }],
                }],
            }],
        },
    }
    stats, team = _parse_player_stats(summary, "202331", "Paul George")
    assert team["abbreviation"] == "BOS"
    assert stats["pts"] == 17
    assert stats["reb"] == 5
    assert stats["ast"] == 3
    assert stats["fg3m"] == 2

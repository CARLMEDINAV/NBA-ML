import app  # noqa: F401 - registers src/ on sys.path for test imports

from nba_ml.lineup_impact_api import (
    LineupImpactIn,
    PlayerIn,
    TeamScenarioIn,
    simulate_lineup_impact,
)


def _player(
    player_id: str, name: str, minutes: float, pts: float,
    position: str = "", availability_status: str = "ACTIVE",
) -> PlayerIn:
    return PlayerIn(
        player_id=player_id, name=name, minutes=minutes, pts=pts,
        reb=5, ast=4, usage=20, position=position,
        availability_status=availability_status,
    )


def _away_team():
    return TeamScenarioIn(
        team="NYK",
        starters=[_player(f"a{i}", f"Away {i}", 30, 10) for i in range(5)],
    )


def test_lineup_impact_recalculates_team_and_game_total():
    home = TeamScenarioIn(
        team="BOS",
        starters=[
            _player(str(i), f"Starter {i}", 32, 20 if i == 1 else 10)
            for i in range(1, 6)
        ],
        bench=[_player("6", "Bench scorer", 18, 12)],
        removed_ids=["1"],
    )
    result = simulate_lineup_impact(
        LineupImpactIn(
            home=home, away=_away_team(),
            baseline={"home_score": 110, "away_score": 108, "total": 218},
        )
    )
    assert result["teams"]["home"]["removed"] == ["Starter 1"]
    assert result["teams"]["home"]["replacements"][0]["in"] == "Bench scorer"
    assert result["scenario"]["total"] < result["baseline"]["total"]
    assert result["changes"]["total"] == result["changes"]["home_points"]


def test_lineup_impact_without_absences_preserves_baseline():
    starters = [_player(str(i), f"Player {i}", 30, 10) for i in range(5)]
    result = simulate_lineup_impact(
        LineupImpactIn(
            home=TeamScenarioIn(team="BOS", starters=starters),
            away=TeamScenarioIn(team="NYK", starters=starters),
            baseline={"home_score": 111, "away_score": 109, "total": 220},
        )
    )
    assert result["scenario"]["total"] == 220
    assert result["changes"]["total"] == 0


def test_confirmed_out_starter_is_removed_automatically_without_manual_id():
    starters = [
        _player("1", "Unavailable starter", 32, 20, "PG", "OUT"),
        *[_player(str(i), f"Starter {i}", 30, 10) for i in range(2, 6)],
    ]
    team = TeamScenarioIn(
        team="BOS",
        starters=starters,
        bench=[_player("6", "Healthy guard", 20, 12, "PG")],
    )
    result = simulate_lineup_impact(
        LineupImpactIn(
            home=team, away=_away_team(),
            baseline={"home_score": 110, "away_score": 108, "total": 218},
        )
    )
    impact = result["teams"]["home"]
    assert impact["removed"] == ["Unavailable starter"]
    assert impact["replacements"][0]["in"] == "Healthy guard"


def test_unavailable_bench_players_are_excluded_from_replacement_pool():
    starters = [
        _player("1", "OUT starter", 32, 20, "PG"),
        *[_player(str(i), f"Starter {i}", 30, 10) for i in range(2, 6)],
    ]
    team = TeamScenarioIn(
        team="BOS",
        starters=starters,
        bench=[
            _player("6", "OUT bench", 24, 18, "PG", "OUT"),
            _player("7", "Questionable bench", 22, 16, "PG", "QUESTIONABLE"),
            _player("8", "Available bench", 18, 9, "PG", "ACTIVE"),
        ],
        removed_ids=["1"],
    )
    result = simulate_lineup_impact(
        LineupImpactIn(
            home=team, away=_away_team(),
            baseline={"home_score": 110, "away_score": 108, "total": 218},
        )
    )
    impact = result["teams"]["home"]
    assert impact["replacements"][0]["in"] == "Available bench"
    assert set(impact["excluded_bench"]) == {"OUT bench", "Questionable bench"}


def test_replacement_prefers_same_position_before_higher_minutes():
    starters = [
        _player("1", "Missing center", 30, 18, "C"),
        *[_player(str(i), f"Starter {i}", 30, 10) for i in range(2, 6)],
    ]
    team = TeamScenarioIn(
        team="BOS",
        starters=starters,
        bench=[
            _player("6", "Guard with more minutes", 28, 14, "PG"),
            _player("7", "Center replacement", 18, 8, "C"),
        ],
        removed_ids=["1"],
    )
    result = simulate_lineup_impact(
        LineupImpactIn(
            home=team, away=_away_team(),
            baseline={"home_score": 110, "away_score": 108, "total": 218},
        )
    )
    assert result["teams"]["home"]["replacements"][0]["in"] == "Center replacement"

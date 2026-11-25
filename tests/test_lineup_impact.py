import app  # noqa: F401 - registers src/ on sys.path for test imports

from nba_ml.lineup_impact_api import (
    LineupImpactIn,
    PlayerIn,
    TeamScenarioIn,
    simulate_lineup_impact,
)


def _player(player_id: str, name: str, minutes: float, pts: float) -> PlayerIn:
    return PlayerIn(
        player_id=player_id, name=name, minutes=minutes, pts=pts,
        reb=5, ast=4, usage=20,
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
    away = TeamScenarioIn(
        team="NYK",
        starters=[_player(f"a{i}", f"Away {i}", 30, 10) for i in range(5)],
    )
    result = simulate_lineup_impact(
        LineupImpactIn(
            home=home,
            away=away,
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

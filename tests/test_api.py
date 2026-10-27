"""Tests de la API publica que se despliega en Vercel."""

from __future__ import annotations

import pandas as pd
from fastapi.testclient import TestClient

import app as app_module
from app import app

client = TestClient(app)


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_meta_and_teams():
    response = client.get("/api/meta", params={"today": "2026-10-21"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["model_name"]
    assert payload["trained_through"] == "2025-26"
    assert len(payload["teams"]) == 30
    assert payload["current_season"] == "2026-27"
    assert payload["handicap_test_metrics"]["mae"] > 0
    assert 0 < payload["handicap_test_metrics"]["winner_accuracy"] <= 1


def test_upcoming_returns_predictions():
    response = client.get(
        "/api/upcoming",
        params={"today": "2027-01-15", "days": 3, "line": 225.5},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 1
    game = payload["games"][0]
    assert 150 < game["predicted_total"] < 320
    assert 0 <= game["probability_over"] <= 1
    assert game["lean"] in {"OVER", "UNDER", "—"}
    assert game["model_handicap"]
    assert game["possible_winner"]
    assert game["predicted_margin"] is not None
    assert abs(
        game["projected_home_score"]
        + game["projected_away_score"]
        - game["predicted_total"]
    ) < 1e-6


def test_upcoming_includes_analysis_text():
    response = client.get(
        "/api/upcoming",
        params={
            "today": "2027-01-15",
            "days": 3,
            "line": 225.5,
            "analysis": 1,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    game = payload["games"][0]
    assert game["analysis"]
    assert "Riesgo" in game["analysis"]
    assert game["total_label"]


def test_upcoming_merges_market_odds(monkeypatch):
    base = client.get(
        "/api/upcoming",
        params={"today": "2027-01-15", "days": 3, "line": 225.5},
    ).json()
    game = base["games"][0]
    market = pd.DataFrame(
        [
            {
                "game_id": game["game_id"],
                "market_spread": -3.5,
                "market_total": 230.5,
                "market_details": "BOS -3.5",
                "market_provider": "TestBook",
                "over_odds": "-110",
                "under_odds": "-108",
            }
        ]
    )
    monkeypatch.setattr(
        app_module, "_fetch_espn_odds", lambda day, schedule: market.copy()
    )
    response = client.get(
        "/api/upcoming",
        params={
            "today": "2027-01-15",
            "days": 3,
            "line": 225.5,
            "odds": 1,
            "analysis": 1,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    matched = next(
        item for item in payload["games"] if item["game_id"] == game["game_id"]
    )
    assert matched["market_spread"] == -3.5
    assert matched["market_total"] == 230.5
    assert matched["market_handicap"]
    assert matched["over_odds"] == "-110"
    assert matched["under_odds"] == "-108"
    assert matched["home_cover_probability"] is not None
    assert matched["market_total_side"] in {"OVER", "UNDER", "PUSH"}
    assert "Mercado" in matched["analysis"]


def test_predict_single_game():
    response = client.get(
        "/api/predict",
        params={
            "home": "BOS",
            "away": "LAL",
            "date": "2027-01-15",
            "line": 225.5,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["home"] == "BOS"
    assert payload["away"] == "LAL"
    assert len(payload["interval_80"]) == 2
    assert payload["lean"] in {"OVER", "UNDER", "—"}
    assert payload["predicted_margin"] is not None
    assert payload["model_handicap"]
    assert payload["analysis"]


def test_predict_with_handicap_cover_probability():
    response = client.get(
        "/api/predict",
        params={
            "home": "BOS",
            "away": "LAL",
            "date": "2027-01-15",
            "line": 225.5,
            "handicap": -4.5,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    home_cover = payload["home_cover_probability"]
    away_cover = payload["away_cover_probability"]
    assert 0 <= home_cover <= 1
    assert abs(home_cover + away_cover - 1) < 1e-9
    assert abs(payload["model_home_spread"] + payload["predicted_margin"]) < 1e-9


def test_predict_rejects_invalid_team():
    response = client.get(
        "/api/predict",
        params={"home": "XXX", "away": "LAL", "date": "2026-10-22"},
    )
    assert response.status_code == 400


def test_players_pra():
    response = client.get(
        "/api/players",
        params={"season": "2025-26", "mode": "pergame", "limit": 5, "min_gp": 10},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] > 100
    assert len(payload["players"]) == 5
    player = payload["players"][0]
    assert player["pra"] == player["pts"] + player["reb"] + player["ast"]
    assert player["player_id"] > 0
    assert player["headshot_url"].startswith(
        "https://cdn.nba.com/headshots/nba/latest/260x190/"
    )


def test_players_current_season_uses_current_rosters():
    current = client.get(
        "/api/players",
        params={"season": "2026-27", "mode": "pergame", "limit": 1, "min_gp": 1},
    )
    assert current.status_code == 200
    assert current.json()["count"] > 250

    response = client.get(
        "/api/players",
        params={
            "season": "2026-27",
            "mode": "pergame",
            "limit": 5,
            "min_gp": 1,
            "q": "Giannis Antetokounmpo",
        },
    )
    assert response.status_code == 200
    players = response.json()["players"]
    giannis = next(
        player for player in players if player["player_name"] == "Giannis Antetokounmpo"
    )
    assert giannis["team"] == "MIA"


def test_player_props_projections():
    response = client.get(
        "/api/player-props",
        params={"today": "2027-01-15", "days": 3, "limit": 5},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 1
    assert payload["player_count"] >= payload["count"]
    assert payload["model_name"] == "Ridge props"
    assert payload["trained_through"] == "2026-27"
    assert payload["pra_metrics"]["mae"] > 0
    assert isinstance(payload["teams_without_games"], list)
    game = payload["games"][0]
    assert len(game["players"]) <= 5
    player = game["players"][0]
    assert player["player_id"] > 0
    assert player["gp"] >= 1
    assert player["projected_pts"] >= 0
    assert player["projected_reb"] >= 0
    assert player["projected_ast"] >= 0
    assert abs(
        player["projected_pra"]
        - (
            player["projected_pts"]
            + player["projected_reb"]
            + player["projected_ast"]
        )
    ) < 1e-9
    assert player["headshot_url"].startswith(
        "https://cdn.nba.com/headshots/nba/latest/260x190/"
    )


def test_player_props_team_filter():
    response = client.get(
        "/api/player-props",
        params={"today": "2027-01-15", "days": 7, "team": "PHI", "limit": 3},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 1
    for game in payload["games"]:
        assert "PHI" in {game["home"], game["away"]}
        for player in game["players"]:
            assert player["team"] in {game["home"], game["away"]}


def test_player_card_detail():
    response = client.get(
        "/api/player-card",
        params={"player_id": 203507, "today": "2027-01-15"},
    )
    assert response.status_code == 200
    payload = response.json()
    player = payload["player"]
    assert player["player_name"] == "Giannis Antetokounmpo"
    assert player["team"] == "MIA"
    assert player["gp"] >= 1
    assert player["headshot_url"].startswith(
        "https://cdn.nba.com/headshots/nba/latest/260x190/"
    )
    assert abs(
        player["averages"]["pra"]
        - (
            player["averages"]["pts"]
            + player["averages"]["reb"]
            + player["averages"]["ast"]
        )
    ) < 0.2
    assert payload["games"]
    game = payload["games"][0]
    assert game["opponent"]
    assert game["date"]
    assert game["pra"] is not None
    assert game["min"] is not None
    assert game["fgm"] is not None
    assert game["fga"] is not None
    assert game["usage"] is not None
    assert payload["upcoming"] is not None
    assert payload["upcoming"]["opponent"]
    assert payload["upcoming"]["projection"]["pra"] > 0
    assert payload["upcoming"]["projection"]["min"] > 0
    efficiency = payload["efficiency"]
    assert 0 <= efficiency["fg_pct"] <= 1
    assert 0 <= efficiency["fg3_pct"] <= 1
    assert 0 <= efficiency["ft_pct"] <= 1
    assert 0 <= efficiency["ts_pct"] <= 2
    assert 0 <= efficiency["usage"] <= 1
    assert efficiency["usage_gp"] >= 1
    h2h = payload["h2h"]
    assert h2h["opponent"] == payload["upcoming"]["opponent"]
    assert h2h["games"] >= 1
    assert h2h["meetings"]
    assert h2h["averages"]["usage"] is not None
    assert payload["defenders"]
    defender = payload["defenders"][0]
    assert defender["player_name"]
    assert defender["matchup_min_pg"] is not None
    assert defender["player_pts_pg"] is not None
    likely = payload["likely_defender"]
    assert likely["team"] == payload["upcoming"]["opponent"]
    assert likely["player_name"]
    assert likely["role"] in {"big", "guard", "wing"}
    assert likely["basis"] in {"historial", "rol"}
    assert likely["confidence"] in {"media", "baja"}
    assert likely["headshot_url"].startswith(
        "https://cdn.nba.com/headshots/nba/latest/260x190/"
    )
    if likely["is_historical"]:
        assert likely["matchup_gp"] >= 1
        assert likely["matchup_min_pg"] is not None
    assert payload["teammates"]
    assert payload["note"]


def test_player_card_unknown_player():
    response = client.get("/api/player-card", params={"player_id": 999999})
    assert response.status_code == 404


def test_results_backtest_2025_26():
    response = client.get(
        "/api/results",
        params={"season": "2025-26", "line": 225.5, "limit": 5},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1225
    assert payload["summary"]["n"] == 1225
    assert len(payload["games"]) == 5
    game = payload["games"][0]
    assert game["actual_result"] in {"OVER", "UNDER", "PUSH"}
    assert game["predicted_margin"] is not None
    assert game["actual_margin"] is not None
    assert game["model_handicap"]
    assert game["possible_winner"]
    assert game["margin_error"] is not None


def test_results_current_season_finals():
    response = client.get(
        "/api/results",
        params={"season": "2026-27", "today": "2026-10-21", "line": 225.5},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 11
    assert payload["summary"]["n"] == payload["count"]
    game = payload["games"][0]
    assert game["actual_margin"] is not None
    assert game["predicted_margin"] is not None
    assert game["model_handicap"]
    assert game["possible_winner"]
    assert game["actual_winner"]


def test_static_frontend_is_served():
    response = client.get("/")
    assert response.status_code == 200
    assert "NBA Totals" in response.text
    assert "Próximos" in response.text
    assert "Análisis" in response.text
    assert "Handicap sugerido" in response.text
    assert "Jugadores" in response.text
    assert "Props" in response.text
    assert "Predicciones de jugadores" in response.text
    assert 'id="playerDetail"' in response.text
    assert 'id="pdEfficiency"' in response.text
    assert 'id="pdH2H"' in response.text
    assert 'id="pdLikelyDefender"' in response.text
    assert 'id="pdDefenders"' in response.text
    assert 'id="upcomingSummary" aria-busy="true"' in response.text
    assert 'class="loading skeleton-row"' in response.text
    assert "no incorpora partidos posteriores ni lesiones" in response.text
    assert "VER ANÁLISIS DEL PARTIDO" in response.text


def test_static_javascript_includes_player_headshots():
    response = client.get("/app.js")
    assert response.status_code == 200
    assert "player-headshot" in response.text
    assert "cdn.nba.com/headshots" in response.text
    assert "/api/player-props" in response.text
    assert "/api/player-card" in response.text
    assert "loadProps" in response.text
    assert "openPlayerDetail" in response.text
    assert "renderDetailEfficiency" in response.text
    assert "renderDetailH2H" in response.text
    assert "renderDetailDefenders" in response.text
    assert "loadingRows" in response.text
    assert "skeleton-row" in response.text
    assert 'label: "MIN"' in response.text


def test_static_styles_reserve_layout_space():
    response = client.get("/styles.css")
    assert response.status_code == 200
    assert "scrollbar-gutter: stable" in response.text
    assert ".skeleton-bar" in response.text
    assert ".control-note" in response.text
    assert "min-height: 1.55em" in response.text

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
        params={"today": "2026-10-22", "days": 3, "line": 225.5},
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
            "today": "2026-10-22",
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
        params={"today": "2026-10-22", "days": 3, "line": 225.5},
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
            "today": "2026-10-22",
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
            "date": "2026-10-22",
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
            "date": "2026-10-22",
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

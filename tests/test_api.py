"""Tests de la API publica que se despliega en Vercel."""

from __future__ import annotations

from fastapi.testclient import TestClient

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


def test_upcoming_returns_predictions():
    response = client.get(
        "/api/upcoming",
        params={"today": "2026-10-21", "days": 1, "line": 225.5},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 1
    game = payload["games"][0]
    assert 150 < game["predicted_total"] < 320
    assert 0 <= game["probability_over"] <= 1
    assert game["lean"] in {"OVER", "UNDER", "—"}


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
    assert payload["games"][0]["actual_result"] in {"OVER", "UNDER", "PUSH"}


def test_results_current_season_finals():
    response = client.get(
        "/api/results",
        params={"season": "2026-27", "today": "2026-10-21", "line": 225.5},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    assert payload["summary"]["n"] == 3


def test_static_frontend_is_served():
    response = client.get("/")
    assert response.status_code == 200
    assert "NBA Totals" in response.text
    assert "Próximos" in response.text

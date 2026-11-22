"""NBA Lab and live total projection API.

These endpoints are additive: they load the existing Ridge artifacts but never
modify or retrain them. Scenario player-minute effects are explicit user inputs;
live projections are a separate, pace-based in-game estimator.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from nba_ml.data.build_dataset import build_feature_row
from nba_ml.models.predict import (
    _state_as_of,
    load_artifact,
    load_team_state,
    predict_game,
    resolve_team,
)

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"
router = APIRouter(tags=["NBA Lab and Live Predictor"])
TIMEOUT = 8.0
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NBA-TOTALS/2.0)", "Accept": "application/json"}


class LabScenarioIn(BaseModel):
    home: str = Field(..., min_length=2, max_length=40)
    away: str = Field(..., min_length=2, max_length=40)
    game_date: date
    line: float | None = Field(None, ge=100, le=350)
    pace_delta_pct: float = Field(0, ge=-20, le=20)
    home_off_rating_delta: float = Field(0, ge=-20, le=20)
    away_off_rating_delta: float = Field(0, ge=-20, le=20)
    home_minutes_delta: float = Field(0, ge=-48, le=48)
    away_minutes_delta: float = Field(0, ge=-48, le=48)
    home_points_per_minute: float = Field(0, ge=0, le=3)
    away_points_per_minute: float = Field(0, ge=0, le=3)


class LiveStateIn(BaseModel):
    home_team: str = Field(..., min_length=2, max_length=8)
    away_team: str = Field(..., min_length=2, max_length=8)
    home_score: int = Field(..., ge=0, le=250)
    away_score: int = Field(..., ge=0, le=250)
    period: int = Field(..., ge=1, le=10)
    clock: str = Field(..., pattern=r"^\d{1,2}:\d{2}$")
    pregame_total: float = Field(225.5, ge=100, le=350)


def _artifacts() -> tuple[dict, pd.DataFrame]:
    try:
        return (
            load_artifact(ARTIFACTS / "model.joblib"),
            load_team_state(ARTIFACTS / "team_state.csv"),
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"Artefactos ML no disponibles: {exc}") from exc


def _minute_points(minutes: float, points_per_minute: float) -> float:
    return float(minutes) * float(points_per_minute)


def _scenario_probability(residuals: Any, threshold: float) -> float | None:
    values = np.asarray(residuals, dtype=float)
    values = values[np.isfinite(values)]
    if not values.size:
        return None
    return float(np.mean(values > threshold))


def simulate_scenario(payload: LabScenarioIn) -> dict[str, Any]:
    artifact, state = _artifacts()
    reference = pd.Timestamp(payload.game_date)
    try:
        baseline = predict_game(
            artifact, state, payload.home, payload.away,
            as_of=reference, line=payload.line, warn_stale=False,
        )
        home = _state_as_of(resolve_team(state, payload.home), reference).copy()
        away = _state_as_of(resolve_team(state, payload.away), reference).copy()

        pace_factor = 1.0 + payload.pace_delta_pct / 100.0
        for team_state in (home, away):
            if "pace_roll10" in team_state:
                team_state["pace_roll10"] = float(team_state["pace_roll10"]) * pace_factor
        if "off_rtg_roll10" in home:
            home["off_rtg_roll10"] = float(home["off_rtg_roll10"]) + payload.home_off_rating_delta
        if "off_rtg_roll10" in away:
            away["off_rtg_roll10"] = float(away["off_rtg_roll10"]) + payload.away_off_rating_delta

        features = build_feature_row(home, away)[artifact["features"]]
        total_before_player_overlay = float(artifact["model"].predict(features)[0])
        margin_model = artifact.get("margin_model")
        margin_features = artifact.get("margin_features", artifact["features"])
        if margin_model is not None:
            margin = float(margin_model.predict(features[margin_features])[0])
        else:
            margin = float(baseline.get("predicted_margin") or 0.0)

        home_score = (total_before_player_overlay + margin) / 2.0
        away_score = (total_before_player_overlay - margin) / 2.0
        home_player_delta = _minute_points(payload.home_minutes_delta, payload.home_points_per_minute)
        away_player_delta = _minute_points(payload.away_minutes_delta, payload.away_points_per_minute)
        home_score += home_player_delta
        away_score += away_player_delta
        simulated_total = home_score + away_score
        simulated_margin = home_score - away_score

        residuals = artifact.get("residuals", [])
        margin_residuals = artifact.get("margin_residuals", [])
        probability_over = (
            _scenario_probability(residuals, float(payload.line) - simulated_total)
            if payload.line is not None else None
        )
        margin_values = np.asarray(margin_residuals, dtype=float)
        margin_values = margin_values[np.isfinite(margin_values)]
        home_win_probability = (
            float(np.mean(margin_values > -simulated_margin)) if margin_values.size else None
        )
        return {
            "matchup": {"home": baseline["home"], "away": baseline["away"], "date": payload.game_date.isoformat()},
            "baseline": {
                "total": float(baseline["predicted_total"]),
                "home_score": baseline.get("projected_home_score"),
                "away_score": baseline.get("projected_away_score"),
                "margin": baseline.get("predicted_margin"),
                "probability_over": baseline.get("probability_over"),
            },
            "scenario": {
                "total": round(simulated_total, 2),
                "home_score": round(home_score, 2),
                "away_score": round(away_score, 2),
                "margin": round(simulated_margin, 2),
                "probability_over": probability_over,
                "home_win_probability": home_win_probability,
            },
            "changes": {
                "total": round(simulated_total - float(baseline["predicted_total"]), 2),
                "home_points_from_minutes": round(home_player_delta, 2),
                "away_points_from_minutes": round(away_player_delta, 2),
            },
            "scenario_inputs": payload.model_dump(mode="json"),
            "methodology": (
                "El modelo Ridge existente vuelve a predecir con ajustes de ritmo y eficiencia. "
                "El impacto de minutos se aplica como overlay explícito usando los PTS/min indicados; "
                "no es una predicción individual de minutos ni un modelo de sustitución."
            ),
            "trained_through": artifact.get("trained_through"),
            "model_name": artifact.get("model_name"),
        }
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def project_live_total(
    home_score: int, away_score: int, period: int, clock: str, pregame_total: float
) -> dict[str, Any]:
    """Blend observed scoring pace with the pregame total; supports overtime."""
    try:
        minute_text, second_text = clock.split(":")
        remaining = int(minute_text) * 60 + int(second_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError("Reloj inválido; usa MM:SS") from exc
    if remaining < 0 or remaining > 12 * 60:
        raise ValueError("El reloj debe estar entre 00:00 y 12:00")

    if period <= 4:
        elapsed_seconds = (period - 1) * 12 * 60 + (12 * 60 - remaining)
        regulation_seconds = 48 * 60
    else:
        elapsed_seconds = 48 * 60 + (period - 5) * 5 * 60 + (5 * 60 - min(remaining, 5 * 60))
        regulation_seconds = 48 * 60 + (period - 4) * 5 * 60

    current = int(home_score) + int(away_score)
    if elapsed_seconds <= 0:
        return {
            "current_total": current, "projected_total": round(float(pregame_total), 1),
            "pace_projection": None, "live_weight": 0.0, "elapsed_minutes": 0.0,
            "remaining_minutes": round(regulation_seconds / 60, 1),
        }

    pace_projection = current * regulation_seconds / elapsed_seconds
    elapsed_minutes = elapsed_seconds / 60.0
    live_weight = min(0.85, elapsed_minutes / (elapsed_minutes + 18.0))
    projected = (1.0 - live_weight) * float(pregame_total) + live_weight * pace_projection
    projected = max(float(current), projected)
    return {
        "current_total": current,
        "projected_total": round(projected, 1),
        "pace_projection": round(pace_projection, 1),
        "live_weight": round(live_weight, 3),
        "elapsed_minutes": round(elapsed_minutes, 1),
        "remaining_minutes": round(max(0.0, (regulation_seconds - elapsed_seconds) / 60), 1),
    }


@router.post("/api/lab/simulate")
def lab_simulate(payload: LabScenarioIn) -> dict[str, Any]:
    return simulate_scenario(payload)


@router.post("/api/live/predict/manual")
def live_predict_manual(payload: LiveStateIn) -> dict[str, Any]:
    try:
        result = project_live_total(
            payload.home_score, payload.away_score, payload.period,
            payload.clock, payload.pregame_total,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "source": "manual",
        "home_team": payload.home_team.upper(),
        "away_team": payload.away_team.upper(),
        "score": {"home": payload.home_score, "away": payload.away_score},
        "period": payload.period,
        "clock": payload.clock,
        **result,
        "methodology": "Proyección híbrida: total prepartido + ritmo observado, con shrinkage hacia el prior prepartido.",
    }


@router.get("/api/live/predict")
def live_predict(
    event_id: str = Query(..., min_length=5, max_length=20),
    pregame_total: float = Query(225.5, ge=100, le=350),
) -> dict[str, Any]:
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
    try:
        response = httpx.get(url, params={"event": event_id}, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo consultar ESPN: {exc}") from exc

    header = payload.get("header") or {}
    competitions = header.get("competitions") or []
    competition = competitions[0] if competitions else {}
    competitors = competition.get("competitors") or []
    home = next((item for item in competitors if item.get("homeAway") == "home"), {})
    away = next((item for item in competitors if item.get("homeAway") == "away"), {})
    status = competition.get("status") or header.get("status") or {}
    period = status.get("period") or (status.get("type") or {}).get("period")
    clock = status.get("displayClock") or status.get("clock")
    if period is None or clock is None or not home or not away:
        raise HTTPException(status_code=502, detail="ESPN no entregó marcador, período y reloj completos para este partido.")
    try:
        result = project_live_total(
            int(home.get("score") or 0), int(away.get("score") or 0),
            int(period), str(clock), pregame_total,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f"Datos en vivo no válidos: {exc}") from exc
    return {
        "source": "ESPN",
        "event_id": event_id,
        "home_team": (home.get("team") or {}).get("abbreviation", ""),
        "away_team": (away.get("team") or {}).get("abbreviation", ""),
        "score": {"home": int(home.get("score") or 0), "away": int(away.get("score") or 0)},
        "period": int(period),
        "clock": str(clock),
        **result,
        "methodology": "Proyección híbrida: total prepartido + ritmo observado, con shrinkage hacia el prior prepartido.",
    }

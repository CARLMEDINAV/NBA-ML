"""Prediccion de totales para un enfrentamiento concreto.

Usa el ultimo estado conocido de cada equipo (``team_state.csv``) y el artefacto
entrenado (``model.joblib``). La probabilidad de over/under se estima de forma
empirica con los residuos fuera de muestra del walk-forward, sin asumir
normalidad.
"""

from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from ..config import ELO_BASE, ELO_SEASON_CARRYOVER, MODELS_DIR, PROCESSED_DIR
from ..data.build_dataset import build_feature_row

LOG = logging.getLogger(__name__)

ARTIFACT_PATH = MODELS_DIR / "model.joblib"
DEAD_DAYS_WARNING = 14


def load_artifact(path: str | Path | None = None) -> dict:
    artifact_path = Path(path) if path else ARTIFACT_PATH
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"No existe {artifact_path}. Ejecuta primero: nba-ml train"
        )
    return joblib.load(artifact_path)


def load_team_state(path: str | Path | None = None) -> pd.DataFrame:
    state_path = Path(path) if path else PROCESSED_DIR / "team_state.csv"
    if not state_path.exists():
        raise FileNotFoundError(
            f"No existe {state_path}. Ejecuta primero: nba-ml build"
        )
    state = pd.read_csv(state_path, dtype={"team_id": int})
    required = {"recent_game_dates", "elo"}
    missing = required - set(state.columns)
    if missing:
        raise ValueError(
            f"team_state.csv desactualizado (faltan {missing}). Ejecuta: nba-ml build"
        )
    return state


def resolve_team(state: pd.DataFrame, query: str) -> pd.Series:
    """Acepta abreviatura (BOS) o nombre completo (Boston Celtics)."""
    text = query.strip()
    exact = state[state["team_abbreviation"].str.upper() == text.upper()]
    if len(exact) == 1:
        return exact.iloc[0]

    matches = state[state["team_name"].str.lower().str.contains(text.lower(), regex=False)]
    if len(matches) == 1:
        return matches.iloc[0]
    if len(matches) > 1:
        options = ", ".join(sorted(matches["team_abbreviation"]))
        raise ValueError(f"Equipo ambiguo '{query}'. Opciones: {options}")
    raise ValueError(f"Equipo no encontrado: '{query}'")


def _season_label(date: pd.Timestamp) -> str:
    """Temporada NBA a la que pertenece una fecha (empieza en septiembre)."""
    year = date.year if date.month >= 9 else date.year - 1
    return f"{year}-{str(year + 1)[-2:]}"


def _state_as_of(state: pd.Series, as_of: pd.Timestamp) -> pd.Series:
    """Recalcula descanso, densidad de calendario y Elo para una fecha dada."""
    dates = pd.to_datetime(str(state["recent_game_dates"]).split(","))
    last_date = dates.max()
    if as_of < last_date:
        raise ValueError(
            f"La fecha {as_of.date()} es anterior al ultimo partido de "
            f"{state['team_abbreviation']} ({last_date.date()})"
        )

    updated = state.copy()
    updated["days_rest"] = float(np.clip((as_of - last_date).days, 0, 10))
    updated["is_b2b"] = int(updated["days_rest"] <= 1)
    updated["games_last_7d"] = int(
        ((dates >= as_of - pd.Timedelta(days=7)) & (dates < as_of)).sum()
    )
    updated["games_last_14d"] = int(
        ((dates >= as_of - pd.Timedelta(days=14)) & (dates < as_of)).sum()
    )
    updated["month"] = as_of.month

    # Si el partido es de una temporada posterior, Elo se regresa a la media y
    # el contador de partidos de temporada empieza de cero (igual que en train).
    if str(state["season"]) != _season_label(as_of):
        updated["elo"] = ELO_BASE + ELO_SEASON_CARRYOVER * (float(state["elo"]) - ELO_BASE)
        updated["season_game_no"] = 1
    return updated


def predict_game(
    artifact: dict,
    state: pd.DataFrame,
    home: str,
    away: str,
    as_of: str | pd.Timestamp | None = None,
    line: float | None = None,
) -> dict[str, object]:
    """Predice el total y, si se da una linea, P(total > linea)."""
    home_state = resolve_team(state, home)
    away_state = resolve_team(state, away)
    if home_state["team_id"] == away_state["team_id"]:
        raise ValueError("El local y el visitante no pueden ser el mismo equipo")

    if as_of is None:
        reference = pd.Timestamp(home_state["last_game_date"]) + pd.Timedelta(days=3)
    else:
        reference = pd.Timestamp(as_of)

    home_state = _state_as_of(home_state, reference)
    away_state = _state_as_of(away_state, reference)

    for team_state in (home_state, away_state):
        last = pd.Timestamp(team_state["last_game_date"])
        gap = (reference - last).days
        if gap > DEAD_DAYS_WARNING:
            LOG.warning(
                "%s no juega desde %s (%s dias). Datos desactualizados; "
                "la prediccion asume descanso maximo.",
                team_state["team_abbreviation"],
                last.date(),
                gap,
            )

    features = build_feature_row(home_state, away_state)
    features = features[artifact["features"]]
    predicted = float(artifact["model"].predict(features)[0])

    residuals = np.asarray(artifact["residuals"], dtype=float)
    lower, upper = np.quantile(residuals, [0.10, 0.90])

    probability_over = None
    if line is not None:
        probability_over = float(np.mean(residuals > (float(line) - predicted)))

    return {
        "home": str(home_state["team_abbreviation"]),
        "away": str(away_state["team_abbreviation"]),
        "as_of": reference.strftime("%Y-%m-%d"),
        "predicted_total": predicted,
        "interval_80": (predicted + float(lower), predicted + float(upper)),
        "line": float(line) if line is not None else None,
        "probability_over": probability_over,
        "residual_sigma": float(artifact["residual_sigma"]),
        "trained_through": artifact["trained_through"],
        "model_name": artifact["model_name"],
        "home_last_game": str(home_state["last_game_date"]),
        "away_last_game": str(away_state["last_game_date"]),
        "home_rest": float(home_state["days_rest"]),
        "away_rest": float(away_state["days_rest"]),
    }


def format_prediction(result: dict[str, object]) -> str:
    low, high = result["interval_80"]
    lines = [
        f"{result['home']} (local) vs {result['away']} (visitante) - {result['as_of']}",
        f"Total previsto: {result['predicted_total']:.1f} puntos",
        f"Intervalo 80%: {low:.1f} - {high:.1f}",
        f"Descanso: {result['home']} {result['home_rest']:.0f} dias | "
        f"{result['away']} {result['away_rest']:.0f} dias",
    ]
    if result["line"] is not None:
        probability = float(result["probability_over"])
        side = "OVER" if probability >= 0.5 else "UNDER"
        lines.append(
            f"Linea {result['line']:.1f}: P(over) = {probability:.1%} -> {side}"
        )
    lines.append(
        f"Modelo {result['model_name']} | datos hasta {result['trained_through']} | "
        f"sigma residuos {result['residual_sigma']:.1f}"
    )
    return "\n".join(lines)

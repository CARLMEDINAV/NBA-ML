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


ANALYSIS_FEATURES = (
    "pace_sum",
    "off_rtg_sum",
    "def_rtg_sum",
    "form_total",
    "exp_total",
    "ot_rate_sum",
    "close_rate_sum",
    "rest_diff",
    "b2b_sum",
    "elo_diff",
    "pts_exp_home",
    "pts_exp_away",
    "opp_pts_exp_home",
    "opp_pts_exp_away",
    "pace_exp_home",
    "pace_exp_away",
)


def predict_game(
    artifact: dict,
    state: pd.DataFrame,
    home: str,
    away: str,
    as_of: str | pd.Timestamp | None = None,
    line: float | None = None,
    handicap: float | None = None,
    warn_stale: bool = True,
) -> dict[str, object]:
    """Predice total, margen/hándicap y, si hay línea, P(total > línea).

    ``handicap`` es la línea del equipo local en notación de apuestas
    (por ejemplo, -4.5 si el local es favorito). No es una cuota oficial.
    """
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

    for team_state in (home_state, away_state) if warn_stale else ():
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

    predicted_margin = None
    home_win_probability = None
    projected_home = None
    projected_away = None
    model_home_spread = None
    margin_residuals = np.asarray(artifact.get("margin_residuals", []), dtype=float)
    margin_model = artifact.get("margin_model")
    if margin_model is not None:
        predicted_margin = float(margin_model.predict(features)[0])
        projected_home = (predicted + predicted_margin) / 2.0
        projected_away = (predicted - predicted_margin) / 2.0
        model_home_spread = -predicted_margin
        if margin_residuals.size:
            home_win_probability = float(np.mean(margin_residuals > -predicted_margin))

    home_cover_probability = None
    away_cover_probability = None
    if handicap is not None and predicted_margin is not None and margin_residuals.size:
        # El local cubre si margen_real + handicap_local > 0.
        home_cover_probability = float(
            np.mean(margin_residuals > ((-float(handicap)) - predicted_margin))
        )
        away_cover_probability = float(1.0 - home_cover_probability)

    feature_values = {
        name: float(features.iloc[0][name])
        for name in ANALYSIS_FEATURES
        if name in features.columns
    }

    return {
        "home": str(home_state["team_abbreviation"]),
        "away": str(away_state["team_abbreviation"]),
        "as_of": reference.strftime("%Y-%m-%d"),
        "predicted_total": predicted,
        "interval_80": (predicted + float(lower), predicted + float(upper)),
        "line": float(line) if line is not None else None,
        "probability_over": probability_over,
        "predicted_margin": predicted_margin,
        "model_home_spread": model_home_spread,
        "projected_home_score": projected_home,
        "projected_away_score": projected_away,
        "home_win_probability": home_win_probability,
        "handicap": float(handicap) if handicap is not None else None,
        "home_cover_probability": home_cover_probability,
        "away_cover_probability": away_cover_probability,
        "margin_sigma": float(artifact.get("margin_sigma", float("nan"))),
        "features": feature_values,
        "residual_sigma": float(artifact["residual_sigma"]),
        "trained_through": artifact["trained_through"],
        "model_name": artifact["model_name"],
        "home_last_game": str(home_state["last_game_date"]),
        "away_last_game": str(away_state["last_game_date"]),
        "home_elo": float(home_state["elo"]),
        "away_elo": float(away_state["elo"]),
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
    if result.get("predicted_margin") is not None:
        margin = float(result["predicted_margin"])
        favorite = result["home"] if margin >= 0 else result["away"]
        handicap_line = round(abs(margin) * 2) / 2
        handicap = (
            f"{favorite} -{handicap_line:.1f}" if handicap_line >= 0.5 else "Pick'em"
        )
        lines.append(
            f"Margen previsto: {result['home']} {margin:+.1f} | "
            f"handicap modelo: {handicap}"
        )
        lines.append(
            f"Puntos proyectados: {result['home']} "
            f"{float(result['projected_home_score']):.1f} - "
            f"{result['away']} {float(result['projected_away_score']):.1f}"
        )
        if result.get("home_win_probability") is not None:
            lines.append(
                f"P(gana {result['home']}) = "
                f"{float(result['home_win_probability']):.1%}"
            )
    lines.append(
        f"Modelo {result['model_name']} | datos hasta {result['trained_through']} | "
        f"sigma residuos {result['residual_sigma']:.1f}"
    )
    return "\n".join(lines)

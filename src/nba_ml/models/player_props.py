"""Modelo de props por jugador: puntos, rebotes, asistencias y PRA.

El modelo usa medias moviles estrictamente anteriores al partido, descanso,
localia, contexto del equipo rival y las medias de la temporada anterior. Se
entrena con game logs de jugador y se guarda en ``artifacts/player_props.joblib``.
La foto de cada jugador para el siguiente partido se guarda en
``artifacts/player_state.csv``.

Es una estimacion estadistica. No incorpora lesiones, alineaciones, minutos
proyectados por un modelo propio ni cuotas de props.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

LOG = logging.getLogger(__name__)

PROP_TARGETS = ("pts", "reb", "ast")
PROP_TARGET_LABELS = {
    "pts": "Puntos",
    "reb": "Rebotes",
    "ast": "Asistencias",
    "pra": "PRA",
}
ROLL_STATS = ("min", "pts", "reb", "ast", "pra", "fga", "fta", "tov")
ROLL_WINDOWS = (3, 5, 10)
VALIDATION_CUTOFF = "2026-02-01"
DEFAULT_ALPHA = 1.0

PROP_FEATURES = [
    "min_roll3",
    "min_roll5",
    "min_roll10",
    "pts_roll3",
    "pts_roll5",
    "pts_roll10",
    "reb_roll3",
    "reb_roll5",
    "reb_roll10",
    "ast_roll3",
    "ast_roll5",
    "ast_roll10",
    "pra_roll3",
    "pra_roll5",
    "pra_roll10",
    "fga_roll5",
    "fta_roll5",
    "tov_roll5",
    "pts_per_min_roll5",
    "reb_per_min_roll5",
    "ast_per_min_roll5",
    "pra_per_min_roll5",
    "gp_season",
    "days_rest",
    "is_b2b",
    "is_home",
    "prev_min",
    "prev_pts",
    "prev_reb",
    "prev_ast",
    "prev_pra",
    "team_off_rtg_roll10",
    "team_pace_roll10",
    "opp_def_rtg_roll10",
    "opp_pace_roll10",
]

PROP_STATE_COLUMNS = [
    "player_id",
    "player_name",
    "team",
    "season",
    "gp_season",
    "last_game_date",
    *[
        f"{stat}_roll{window}"
        for stat in ROLL_STATS
        for window in ROLL_WINDOWS
    ],
    "pts_per_min_roll5",
    "reb_per_min_roll5",
    "ast_per_min_roll5",
    "pra_per_min_roll5",
    "prev_min",
    "prev_pts",
    "prev_reb",
    "prev_ast",
    "prev_pra",
]

TARGET_CAPS = {"pts": 65.0, "reb": 32.0, "ast": 25.0}


def _opponent(matchup: str) -> str:
    text = str(matchup or "")
    if " vs. " in text:
        return text.split(" vs. ", 1)[1].strip()
    if " @ " in text:
        return text.split(" @ ", 1)[1].strip()
    return ""


def _previous_season(season: str) -> str:
    try:
        year = int(str(season)[:4]) - 1
    except (TypeError, ValueError):
        return ""
    return f"{year}-{str(year + 1)[-2:]}"


def _headshot_url(player_id: Any) -> str | None:
    if player_id is None or pd.isna(player_id):
        return None
    return f"https://cdn.nba.com/headshots/nba/latest/260x190/{int(player_id)}.png"


def build_team_pregame(team_games: pd.DataFrame) -> pd.DataFrame:
    """Contexto de equipo disponible antes de cada partido."""
    frame = team_games.copy()
    frame["game_date"] = pd.to_datetime(frame["game_date"])
    frame["game_id"] = frame["game_id"].astype(str).str.zfill(10)
    frame["opponent"] = frame["matchup"].map(_opponent)
    frame = frame.sort_values(
        ["team_abbreviation", "game_date", "game_id"]
    ).reset_index(drop=True)
    grouped = frame.groupby("team_abbreviation", sort=False)
    for stat in ("off_rtg", "def_rtg", "pace"):
        frame[f"{stat}_roll10"] = grouped[stat].transform(
            lambda series: series.shift(1).rolling(10, min_periods=1).mean()
        )
    return frame[
        [
            "game_id",
            "team_abbreviation",
            "opponent",
            "game_date",
            "off_rtg_roll10",
            "def_rtg_roll10",
            "pace_roll10",
        ]
    ]


def build_prior_averages(stats: pd.DataFrame) -> pd.DataFrame:
    """Convierte una tabla de promedios de temporada en features ``prev_*``."""
    required = {"player_id", "min", "pts", "reb", "ast"}
    missing = required - set(stats.columns)
    if missing:
        raise ValueError(f"Faltan columnas para promedios previos: {sorted(missing)}")
    frame = stats.copy()
    if "pra" not in frame.columns:
        frame["pra"] = frame[["pts", "reb", "ast"]].sum(axis=1)
    frame["player_id"] = pd.to_numeric(frame["player_id"], errors="coerce")
    frame = frame[frame["player_id"].notna()].copy()
    frame["player_id"] = frame["player_id"].astype(int)
    grouped = frame.groupby("player_id", as_index=False).agg(
        prev_min=("min", "mean"),
        prev_pts=("pts", "mean"),
        prev_reb=("reb", "mean"),
        prev_ast=("ast", "mean"),
        prev_pra=("pra", "mean"),
    )
    return grouped


def build_player_features(
    logs: pd.DataFrame,
    pregame: bool = True,
    prior_averages: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Construye features por jugador y partido.

    ``pregame=True`` usa solo informacion anterior al partido (para entrenar y
    evaluar). ``pregame=False`` incluye el partido mas reciente y sirve para
    construir la foto con la que se predice el siguiente partido.
    """
    frame = logs.copy()
    frame["game_date"] = pd.to_datetime(frame["game_date"])
    frame["game_id"] = frame["game_id"].astype(str).str.zfill(10)
    frame["player_id"] = pd.to_numeric(frame["player_id"], errors="coerce")
    frame = frame[frame["player_id"].notna()].copy()
    frame["player_id"] = frame["player_id"].astype(int)
    frame["team"] = frame["team"].astype(str).str.upper().str.strip()
    frame = frame.sort_values(
        ["player_id", "game_date", "game_id"]
    ).reset_index(drop=True)

    grouped = frame.groupby(["player_id", "season"], sort=False)
    for stat in ROLL_STATS:
        for window in ROLL_WINDOWS:
            column = f"{stat}_roll{window}"
            if pregame:
                frame[column] = grouped[stat].transform(
                    lambda series, w=window: series.shift(1)
                    .rolling(w, min_periods=1)
                    .mean()
                )
            else:
                frame[column] = grouped[stat].transform(
                    lambda series, w=window: series.rolling(
                        w, min_periods=1
                    ).mean()
                )

    if pregame:
        frame["gp_season"] = grouped.cumcount()
        frame["days_rest"] = grouped["game_date"].transform(
            lambda series: series.diff().dt.days.clip(lower=0, upper=7)
        )
        frame["days_rest"] = frame["days_rest"].fillna(7.0)
        frame["is_b2b"] = (frame["days_rest"] <= 1).astype(int)
        frame["is_home"] = frame["is_home"].astype(int)
    else:
        frame["gp_season"] = grouped.cumcount() + 1
        frame["days_rest"] = np.nan
        frame["is_b2b"] = 0
        frame["is_home"] = 0

    for stat in ("pts", "reb", "ast", "pra"):
        frame[f"{stat}_per_min_roll5"] = frame[f"{stat}_roll5"] / frame[
            "min_roll5"
        ].clip(lower=1)

    if prior_averages is None:
        frame["prev_season"] = frame["season"].map(_previous_season)
        averages = (
            frame.groupby(["season", "player_id"], as_index=False)
            .agg(
                prev_min=("min", "mean"),
                prev_pts=("pts", "mean"),
                prev_reb=("reb", "mean"),
                prev_ast=("ast", "mean"),
                prev_pra=("pra", "mean"),
            )
            .rename(columns={"season": "prev_season"})
        )
        frame = frame.merge(
            averages, on=["prev_season", "player_id"], how="left"
        )
    else:
        frame = frame.merge(prior_averages, on="player_id", how="left")
    return frame


def attach_team_features(
    features: pd.DataFrame, team_games: pd.DataFrame
) -> pd.DataFrame:
    """Agrega contexto propio y del rival a las features de jugador."""
    team_pre = build_team_pregame(team_games)
    own = team_pre.drop(columns=["opponent"]).rename(
        columns={
            "team_abbreviation": "team",
            "off_rtg_roll10": "team_off_rtg_roll10",
            "pace_roll10": "team_pace_roll10",
        }
    )[
        [
            "game_id",
            "team",
            "team_off_rtg_roll10",
            "team_pace_roll10",
        ]
    ]
    opp = team_pre.drop(columns=["opponent"]).rename(
        columns={
            "team_abbreviation": "opponent",
            "def_rtg_roll10": "opp_def_rtg_roll10",
            "pace_roll10": "opp_pace_roll10",
        }
    )[
        [
            "game_id",
            "opponent",
            "opp_def_rtg_roll10",
            "opp_pace_roll10",
        ]
    ]
    frame = features.copy()
    frame["game_id"] = frame["game_id"].astype(str).str.zfill(10)
    return frame.merge(own, on=["game_id", "team"], how="left").merge(
        opp, on=["game_id", "opponent"], how="left"
    )


def _make_model(alpha: float) -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", Ridge(alpha=alpha)),
        ]
    )


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    residual = actual - predicted
    denominator = float(np.sum((actual - actual.mean()) ** 2))
    r2 = 1.0 - float(np.sum(residual**2)) / denominator if denominator else 0.0
    return {
        "mae": float(np.mean(np.abs(residual))),
        "rmse": float(np.sqrt(np.mean(residual**2))),
        "r2": r2,
        "sigma": float(np.std(residual, ddof=1)) if residual.size > 1 else 0.0,
    }


def train_player_models(
    dataset: pd.DataFrame,
    alpha: float = DEFAULT_ALPHA,
    cutoff: str = VALIDATION_CUTOFF,
) -> dict[str, Any]:
    """Entrena y evalua PTS, REB y AST; PRA se obtiene como suma."""
    frame = dataset.copy()
    frame["game_date"] = pd.to_datetime(frame["game_date"])
    frame = frame[frame["min"].fillna(0) > 0].copy()
    cutoff_ts = pd.Timestamp(cutoff)

    train = frame[frame["game_date"] < cutoff_ts].copy()
    test = frame[
        (frame["game_date"] >= cutoff_ts) & (frame["season"] == "2025-26")
    ].copy()
    if train.empty or test.empty:
        raise ValueError("No hay suficientes datos para entrenar props")

    metrics_by_target: dict[str, dict[str, float]] = {}
    validation_models: dict[str, Pipeline] = {}
    for target in PROP_TARGETS:
        model = _make_model(alpha)
        model.fit(train[PROP_FEATURES], train[target])
        predicted = model.predict(test[PROP_FEATURES])
        actual = test[target].to_numpy(dtype=float)
        metrics = _metrics(actual, predicted)
        metrics["baseline_mae"] = float(
            np.mean(np.abs(actual - float(train[target].mean())))
        )
        metrics["n"] = int(len(test))
        metrics_by_target[target] = metrics
        validation_models[target] = model

    pra_predicted = np.zeros(len(test), dtype=float)
    for target in PROP_TARGETS:
        pra_predicted += validation_models[target].predict(test[PROP_FEATURES])
    pra_actual = test["pra"].to_numpy(dtype=float)
    pra_metrics = _metrics(pra_actual, pra_predicted)
    pra_metrics["baseline_mae"] = float(
        np.mean(np.abs(pra_actual - float(train["pra"].mean())))
    )
    pra_metrics["n"] = int(len(test))

    production_models: dict[str, Pipeline] = {}
    for target in PROP_TARGETS:
        model = _make_model(alpha)
        model.fit(frame[PROP_FEATURES], frame[target])
        production_models[target] = model

    return {
        "model_name": "Ridge props",
        "trained_through": str(frame["season"].max()),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "features": PROP_FEATURES,
        "targets": list(PROP_TARGETS),
        "alpha": alpha,
        "models": production_models,
        "metrics": {
            "targets": metrics_by_target,
            "pra": pra_metrics,
            "validation": {
                "from": cutoff_ts.strftime("%Y-%m-%d"),
                "to": str(test["game_date"].max().date()),
                "season": "2025-26",
                "rows": int(len(test)),
                "method": (
                    "Entrenamiento con partidos anteriores al corte; prueba "
                    "temporal con el resto de 2025-26. PRA = PTS + REB + AST."
                ),
            },
        },
        "caveat": (
            "Proyecciones por partido basadas en historial reciente y contexto "
            "del rival. No incluyen lesiones, alineaciones ni cuotas de props."
        ),
    }


def build_player_state(
    current_logs: pd.DataFrame,
    prior_averages: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Foto mas reciente de cada jugador para predecir su proximo partido."""
    features = build_player_features(
        current_logs, pregame=False, prior_averages=prior_averages
    )
    features = features.sort_values(
        ["player_id", "game_date", "game_id"]
    )
    state = features.groupby("player_id", sort=False).tail(1).copy()
    state = state.rename(columns={"game_date": "last_game_date"})
    columns = [column for column in PROP_STATE_COLUMNS if column in state.columns]
    state = state[columns].reset_index(drop=True)
    state["player_id"] = state["player_id"].astype(int)
    return state


def predict_player_props(
    artifact: dict[str, Any],
    player_state: pd.DataFrame,
    team_state: pd.DataFrame,
    home: str,
    away: str,
    game_date: str | pd.Timestamp,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Proyecta PTS, REB, AST y PRA de los jugadores de un partido."""
    home = str(home).upper().strip()
    away = str(away).upper().strip()
    if home == away:
        return []
    players = player_state[
        player_state["team"].astype(str).str.upper().isin([home, away])
    ].copy()
    if players.empty:
        return []

    teams = team_state.copy()
    teams["team_abbreviation"] = (
        teams["team_abbreviation"].astype(str).str.upper().str.strip()
    )
    teams = teams.set_index("team_abbreviation")
    league_means = {
        column: float(teams[column].mean())
        for column in (
            "off_rtg_roll10",
            "def_rtg_roll10",
            "pace_roll10",
        )
        if column in teams.columns
    }

    game_day = pd.Timestamp(game_date).normalize()
    last_game = pd.to_datetime(players["last_game_date"]).dt.normalize()
    days_rest = (game_day - last_game).dt.days.clip(lower=0, upper=7)
    players["days_rest"] = days_rest.fillna(7.0)
    players["is_b2b"] = (players["days_rest"] <= 1).astype(int)
    players["is_home"] = players["team"].astype(str).str.upper().eq(home).astype(int)
    players["opponent"] = np.where(
        players["team"].astype(str).str.upper().eq(home), away, home
    )

    for column in ("off_rtg_roll10", "pace_roll10"):
        players[f"team_{column}"] = players["team"].map(teams[column])
        players[f"team_{column}"] = players[f"team_{column}"].fillna(
            league_means.get(column, 0.0)
        )
    players["opp_def_rtg_roll10"] = players["opponent"].map(
        teams["def_rtg_roll10"]
    )
    players["opp_def_rtg_roll10"] = players["opp_def_rtg_roll10"].fillna(
        league_means.get("def_rtg_roll10", 0.0)
    )
    players["opp_pace_roll10"] = players["opponent"].map(teams["pace_roll10"])
    players["opp_pace_roll10"] = players["opp_pace_roll10"].fillna(
        league_means.get("pace_roll10", 0.0)
    )

    features = artifact["features"]
    matrix = players.reindex(columns=features)
    for target in PROP_TARGETS:
        predicted = artifact["models"][target].predict(matrix)
        players[f"projected_{target}"] = np.round(
            np.clip(predicted, 0.0, TARGET_CAPS[target]), 1
        )
    players["projected_pra"] = (
        players["projected_pts"]
        + players["projected_reb"]
        + players["projected_ast"]
    ).round(1)
    players = players.sort_values(
        ["projected_pra", "projected_pts"], ascending=False
    ).head(max(1, int(limit)))

    rows: list[dict[str, Any]] = []
    for player in players.itertuples(index=False):
        projected_min = getattr(player, "min_roll5", None)
        rows.append(
            {
                "player_id": int(player.player_id),
                "player_name": str(player.player_name),
                "team": str(player.team).upper(),
                "opponent": str(player.opponent).upper(),
                "is_home": bool(player.is_home),
                "gp": int(player.gp_season),
                "projected_min": (
                    None
                    if projected_min is None or not np.isfinite(projected_min)
                    else round(float(projected_min), 1)
                ),
                "projected_pts": round(float(player.projected_pts), 1),
                "projected_reb": round(float(player.projected_reb), 1),
                "projected_ast": round(float(player.projected_ast), 1),
                "projected_pra": round(float(player.projected_pra), 1),
                "headshot_url": _headshot_url(player.player_id),
            }
        )
    return rows


def load_player_props_artifact(path: str | Path) -> dict[str, Any]:
    artifact_path = Path(path)
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"No existe {artifact_path}. Ejecuta primero: "
            "python scripts/train_player_props.py"
        )
    artifact = joblib.load(artifact_path)
    missing = set(PROP_FEATURES) - set(artifact.get("features", []))
    if missing:
        raise ValueError(
            f"player_props.joblib desactualizado; faltan features {sorted(missing)}"
        )
    return artifact


def load_player_state(path: str | Path) -> pd.DataFrame:
    state_path = Path(path)
    if not state_path.exists():
        raise FileNotFoundError(
            f"No existe {state_path}. Ejecuta primero: "
            "python scripts/update_web_player_props.py"
        )
    state = pd.read_csv(
        state_path,
        dtype={"player_id": int},
        parse_dates=["last_game_date"],
    )
    required = {
        "player_id",
        "player_name",
        "team",
        "gp_season",
        "last_game_date",
        *[
            f"{stat}_roll{window}"
            for stat in ROLL_STATS
            for window in ROLL_WINDOWS
        ],
    }
    missing = required - set(state.columns)
    if missing:
        raise ValueError(f"player_state.csv incompleto; faltan {sorted(missing)}")
    return state

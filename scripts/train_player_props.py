"""Entrena el modelo de props por jugador y publica la foto actual.

Uso:
    python scripts/train_player_props.py

Descarga (o reutiliza la cache de) los game logs de jugador 2023-24 a 2026-27,
entrena PTS, REB y AST con un corte temporal, evalua con el resto de 2025-26 y
guarda ``artifacts/player_props.joblib`` y ``artifacts/player_state.csv``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nba_ml.config import PROCESSED_DIR  # noqa: E402
from nba_ml.data.player_logs import load_player_logs  # noqa: E402
from nba_ml.models.player_props import (  # noqa: E402
    PROP_TARGETS,
    attach_team_features,
    build_player_features,
    build_player_state,
    build_prior_averages,
    train_player_models,
)

ARTIFACTS = ROOT / "artifacts"
MODELS = ROOT / "models"
SEASONS = ("2023-24", "2024-25", "2025-26", "2026-27")
CURRENT_SEASON = "2026-27"
PRIOR_SEASON = "2025-26"


def main() -> int:
    logs = load_player_logs(SEASONS)
    print(f"Game logs: {len(logs)} filas · {logs['player_id'].nunique()} jugadores")

    team_games = pd.read_csv(PROCESSED_DIR / "team_games.csv")
    team_games["game_id"] = team_games["game_id"].astype(str).str.zfill(10)
    features = build_player_features(logs, pregame=True)
    features = attach_team_features(features, team_games)
    print(f"Features de entrenamiento: {features.shape}")

    artifact = train_player_models(features)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, ARTIFACTS / "player_props.joblib")
    joblib.dump(artifact, MODELS / "player_props.joblib")

    prior_stats = pd.read_csv(
        ARTIFACTS / f"player_stats_{PRIOR_SEASON.replace('-', '_')}_pergame.csv"
    )
    prior_averages = build_prior_averages(prior_stats)
    current = logs[logs["season"] == CURRENT_SEASON].copy()
    state = build_player_state(current, prior_averages)
    state.to_csv(ARTIFACTS / "player_state.csv", index=False)

    metrics = artifact["metrics"]
    print("\nMetricas de validacion (2025-26, fuera de muestra):")
    for target in PROP_TARGETS:
        values = metrics["targets"][target]
        print(
            f"  {target.upper():>3}: MAE {values['mae']:.2f} · "
            f"RMSE {values['rmse']:.2f} · R2 {values['r2']:.3f} · "
            f"baseline {values['baseline_mae']:.2f}"
        )
    pra = metrics["pra"]
    print(
        f"  PRA: MAE {pra['mae']:.2f} · RMSE {pra['rmse']:.2f} · "
        f"R2 {pra['r2']:.3f} · baseline {pra['baseline_mae']:.2f}"
    )
    print(
        f"\nEstado actual: {len(state)} jugadores de {CURRENT_SEASON} · "
        f"ultimo partido {state['last_game_date'].max().date()}"
    )
    print(f"Artefacto: {ARTIFACTS / 'player_props.joblib'}")
    print(f"Estado:    {ARTIFACTS / 'player_state.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

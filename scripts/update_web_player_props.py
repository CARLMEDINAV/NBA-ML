"""Actualiza la foto de jugadores que usa el modelo de props en la web.

Se ejecuta en GitHub Actions. Descarga los game logs de la temporada actual y
reconstruye ``artifacts/player_state.csv`` con las medias moviles mas recientes.
No reentrena el modelo: usa los promedios de 2025-26 ya publicados para las
features de temporada anterior.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nba_ml.data.player_logs import load_player_logs  # noqa: E402
from nba_ml.models.player_props import (  # noqa: E402
    build_player_state,
    build_prior_averages,
)

ARTIFACTS = ROOT / "artifacts"
CURRENT_SEASON = "2026-27"
PRIOR_SEASON = "2025-26"


def main() -> int:
    logs = load_player_logs([CURRENT_SEASON], refresh=True)
    prior_stats = pd.read_csv(
        ARTIFACTS / f"player_stats_{PRIOR_SEASON.replace('-', '_')}_pergame.csv"
    )
    prior_averages = build_prior_averages(prior_stats)
    state = build_player_state(logs, prior_averages)
    destination = ARTIFACTS / "player_state.csv"
    state.to_csv(destination, index=False)
    print(
        f"{destination.relative_to(ROOT)}: {len(state)} jugadores · "
        f"{int(state['gp_season'].max())} partidos maximos · "
        f"ultimo {state['last_game_date'].max().date()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

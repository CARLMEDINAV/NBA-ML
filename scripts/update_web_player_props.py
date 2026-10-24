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
HISTORY = ARTIFACTS / "player_game_logs_recent.csv"
HISTORY_COLUMNS = [
    "season",
    "player_id",
    "player_name",
    "team",
    "game_id",
    "game_date",
    "matchup",
    "opponent",
    "is_home",
    "wl",
    "min",
    "pts",
    "reb",
    "ast",
    "stl",
    "blk",
    "fg3m",
    "fga",
    "fta",
    "tov",
    "pra",
]
HISTORY_GAMES = 15


def _update_history(current_logs: pd.DataFrame, player_ids: set[int]) -> pd.DataFrame:
    """Mantiene una foto corta de partidos para la ficha visual del jugador.

    El historico anterior se conserva en el artefacto; solo se reemplazan los
    partidos de la temporada actual para no descargar 2025-26 en cada workflow.
    """
    current = current_logs.copy()
    current["game_date"] = pd.to_datetime(current["game_date"])
    current["game_id"] = current["game_id"].astype(str).str.zfill(10)

    if HISTORY.exists():
        previous = pd.read_csv(
            HISTORY,
            dtype={"player_id": int, "game_id": str},
            parse_dates=["game_date"],
        )
        previous = previous[previous["season"].astype(str).ne(CURRENT_SEASON)]
    else:
        previous = pd.DataFrame(columns=HISTORY_COLUMNS)

    combined = pd.concat([previous, current], ignore_index=True, sort=False)
    combined = combined[combined["player_id"].isin(player_ids)].copy()
    for column in HISTORY_COLUMNS:
        if column not in combined.columns:
            combined[column] = None
    combined = combined[HISTORY_COLUMNS]
    combined = combined.sort_values(["player_id", "game_date", "game_id"])
    combined = combined.groupby("player_id", sort=False, group_keys=False).tail(
        HISTORY_GAMES
    )

    temporary = HISTORY.with_suffix(".tmp")
    combined.to_csv(temporary, index=False)
    temporary.replace(HISTORY)
    return combined


def main() -> int:
    destination = ARTIFACTS / "player_state.csv"
    try:
        logs = load_player_logs([CURRENT_SEASON], refresh=True)
        prior_stats = pd.read_csv(
            ARTIFACTS / f"player_stats_{PRIOR_SEASON.replace('-', '_')}_pergame.csv"
        )
        prior_averages = build_prior_averages(prior_stats)
        state = build_player_state(logs, prior_averages)
        temporary = destination.with_suffix(".tmp")
        state.to_csv(temporary, index=False)
        temporary.replace(destination)
    except Exception as error:  # noqa: BLE001 - no romper el workflow por un timeout
        print(f"::warning::No se pudo actualizar el estado de props: {error}")
        if destination.exists():
            print("Se conserva el estado de props anterior.")
            return 0
        return 1

    try:
        history = _update_history(logs, set(state["player_id"].astype(int)))
    except Exception as error:  # noqa: BLE001 - la ficha visual no bloquea props
        print(f"::warning::No se pudo actualizar el historico visual: {error}")
        if not HISTORY.exists():
            return 1
        history = pd.DataFrame()

    print(
        f"{destination.relative_to(ROOT)}: {len(state)} jugadores · "
        f"{int(state['gp_season'].max())} partidos maximos · "
        f"ultimo {state['last_game_date'].max().date()}"
    )
    if not history.empty:
        print(
            f"{HISTORY.relative_to(ROOT)}: {len(history)} partidos recientes "
            f"para {history['player_id'].nunique()} jugadores"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

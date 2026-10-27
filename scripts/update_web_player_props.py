"""Actualiza la foto de jugadores que usa el modelo de props en la web.

Se ejecuta en GitHub Actions. Agrega a ``player_game_logs_recent.csv`` los
boxscores de ESPN que aun no esten en el historico y reconstruye
``artifacts/player_state.csv`` con las medias moviles mas recientes. No
reentrena el modelo: usa los promedios de 2025-26 ya publicados para las
features de temporada anterior.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nba_ml.data.espn import append_recent_games  # noqa: E402
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
    "fgm",
    "fga",
    "fg_pct",
    "fg3m",
    "fg3a",
    "fg3_pct",
    "ftm",
    "fta",
    "ft_pct",
    "efg_pct",
    "ts_pct",
    "usage",
    "tov",
    "pra",
]
HISTORY_GAMES = 40


def _ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Division segura: deja nulo cuando no hay intentos o minutos."""
    result = numerator.div(denominator)
    return result.where(denominator.gt(0))


def _enrich_history(logs: pd.DataFrame) -> pd.DataFrame:
    """Agrega efectividad de tiro y uso estimado a los game logs.

    El uso sigue la formula de la NBA: porcentaje de posesiones del equipo que
    termina el jugador mientras esta en cancha. Se calcula con los totales del
    equipo en ese partido, reconstruidos a partir de todos los jugadores.
    """
    frame = logs.copy()
    frame["game_id"] = frame["game_id"].astype(str).str.zfill(10)
    frame["team"] = frame["team"].astype(str).str.upper().str.strip()
    numeric = [
        "min",
        "pts",
        "fgm",
        "fga",
        "fg3m",
        "fg3a",
        "ftm",
        "fta",
        "tov",
    ]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    team = (
        frame.groupby(["game_id", "team"], as_index=False)
        .agg(
            team_fga=("fga", "sum"),
            team_fta=("fta", "sum"),
            team_tov=("tov", "sum"),
            team_min=("min", "sum"),
        )
    )
    frame = frame.merge(team, on=["game_id", "team"], how="left")

    frame["fg_pct"] = _ratio(frame["fgm"], frame["fga"])
    frame["fg3_pct"] = _ratio(frame["fg3m"], frame["fg3a"])
    frame["ft_pct"] = _ratio(frame["ftm"], frame["fta"])
    frame["efg_pct"] = _ratio(frame["fgm"] + 0.5 * frame["fg3m"], frame["fga"])
    frame["ts_pct"] = _ratio(
        frame["pts"],
        2 * (frame["fga"] + 0.44 * frame["fta"]),
    )

    possessions = frame["fga"] + 0.44 * frame["fta"] + frame["tov"]
    team_possessions = (
        frame["team_fga"] + 0.44 * frame["team_fta"] + frame["team_tov"]
    )
    frame["usage"] = _ratio(
        possessions * (frame["team_min"] / 5),
        frame["min"] * team_possessions,
    ).clip(upper=1.0)
    return frame


def _update_history(current_logs: pd.DataFrame, player_ids: set[int]) -> pd.DataFrame:
    """Mantiene una foto corta de partidos para la ficha visual del jugador.

    El historico anterior se conserva en el artefacto; solo se reemplazan los
    partidos de la temporada actual para no descargar 2025-26 en cada workflow.
    """
    current = _enrich_history(current_logs)
    current["game_date"] = pd.to_datetime(current["game_date"])

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


def _load_history() -> pd.DataFrame:
    if not HISTORY.exists():
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    return pd.read_csv(
        HISTORY,
        dtype={"player_id": int, "game_id": str},
        parse_dates=["game_date"],
    )


def _current_logs(history: pd.DataFrame) -> pd.DataFrame:
    logs = history[
        history["season"].astype(str).eq(CURRENT_SEASON)
    ].copy()
    if logs.empty:
        raise ValueError(
            f"No hay game logs de {CURRENT_SEASON} en {HISTORY.name}"
        )
    logs["game_date"] = pd.to_datetime(logs["game_date"], errors="coerce")
    logs = logs.dropna(subset=["game_date", "player_id"])
    logs["player_id"] = logs["player_id"].astype(int)
    logs["game_id"] = logs["game_id"].astype(str)
    return logs


def main() -> int:
    destination = ARTIFACTS / "player_state.csv"
    try:
        history = _load_history()
        schedule_path = ARTIFACTS / f"schedule_{CURRENT_SEASON.replace('-', '_')}.csv"
        schedule = (
            pd.read_csv(schedule_path, dtype={"game_id": str})
            if schedule_path.exists()
            else None
        )
        history = append_recent_games(
            history,
            schedule,
            CURRENT_SEASON,
            days_back=14,
        )
        logs = _current_logs(history)
        prior_stats = pd.read_csv(
            ARTIFACTS / f"player_stats_{PRIOR_SEASON.replace('-', '_')}_pergame.csv"
        )
        prior_averages = build_prior_averages(prior_stats)
        state = build_player_state(logs, prior_averages)
        temporary = destination.with_suffix(".tmp")
        state.to_csv(temporary, index=False)
        temporary.replace(destination)
    except Exception as error:  # noqa: BLE001 - no romper el workflow por red
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

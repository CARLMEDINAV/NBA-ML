"""Game logs por jugador (puntos, rebotes, asistencias y PRA).

Los datos se descargan con ``PlayerGameLogs`` y se cachean en
``data/raw/player_game_logs_<temporada>.csv``. El modelo de props usa estos
logs para construir medias moviles estrictamente anteriores al partido.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from ..config import RAW_DIR, SEASON_TYPE

LOG = logging.getLogger(__name__)

PLAYER_LOG_COLUMNS = [
    "season",
    "player_id",
    "player_name",
    "team_id",
    "team",
    "game_id",
    "game_date",
    "matchup",
    "opponent",
    "is_home",
    "wl",
    "min",
    "fgm",
    "fga",
    "fg_pct",
    "fg3m",
    "fg3a",
    "fg3_pct",
    "ftm",
    "fta",
    "ft_pct",
    "oreb",
    "dreb",
    "reb",
    "ast",
    "stl",
    "blk",
    "tov",
    "pf",
    "pts",
    "plus_minus",
    "pra",
]

NUMERIC_COLUMNS = [
    "min",
    "fgm",
    "fga",
    "fg_pct",
    "fg3m",
    "fg3a",
    "fg3_pct",
    "ftm",
    "fta",
    "ft_pct",
    "oreb",
    "dreb",
    "reb",
    "ast",
    "stl",
    "blk",
    "tov",
    "pf",
    "pts",
    "plus_minus",
    "pra",
]


def player_log_path(season: str) -> Path:
    return RAW_DIR / f"player_game_logs_{season.replace('-', '_')}.csv"


def _opponent(matchup: str, team: str) -> str:
    text = str(matchup or "")
    if " vs. " in text:
        return text.split(" vs. ", 1)[1].strip()
    if " @ " in text:
        return text.split(" @ ", 1)[1].strip()
    return ""


def normalize_player_logs(raw: pd.DataFrame, season: str) -> pd.DataFrame:
    """Pasa la salida de ``PlayerGameLogs`` a un contrato estable."""
    if raw.empty:
        return pd.DataFrame(columns=PLAYER_LOG_COLUMNS)

    frame = raw.copy()
    frame.columns = [str(column).lower() for column in frame.columns]
    frame["season"] = season
    frame["game_date"] = pd.to_datetime(frame["game_date"])
    frame["game_id"] = frame["game_id"].astype(str).str.zfill(10)
    frame["player_id"] = pd.to_numeric(frame["player_id"], errors="coerce")
    frame["team_id"] = pd.to_numeric(frame["team_id"], errors="coerce")
    frame["team"] = (
        frame["team_abbreviation"].fillna("").astype(str).str.upper().str.strip()
    )
    frame = frame[frame["player_id"].notna() & frame["team"].ne("")].copy()
    frame["player_id"] = frame["player_id"].astype(int)
    frame["team_id"] = frame["team_id"].fillna(0).astype(int)

    for column in NUMERIC_COLUMNS:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["min"] = frame["min"].fillna(0.0)
    frame = frame[frame["min"] > 0].copy()
    frame["pts"] = frame["pts"].fillna(0.0)
    frame["reb"] = frame["reb"].fillna(0.0)
    frame["ast"] = frame["ast"].fillna(0.0)
    frame["pra"] = frame[["pts", "reb", "ast"]].sum(axis=1)
    frame["is_home"] = frame["matchup"].str.contains("vs.", regex=False)
    frame["opponent"] = [
        _opponent(matchup, team)
        for matchup, team in zip(frame["matchup"], frame["team"], strict=False)
    ]
    frame["player_name"] = frame["player_name"].fillna("").astype(str).str.strip()

    columns = [column for column in PLAYER_LOG_COLUMNS if column in frame.columns]
    frame = frame[columns].sort_values(
        ["game_date", "game_id", "player_id"]
    ).reset_index(drop=True)
    return frame


def fetch_player_season(
    season: str,
    season_type: str = SEASON_TYPE,
    timeout: int = 90,
    retries: int = 3,
) -> pd.DataFrame:
    """Descarga una temporada de game logs por jugador con reintentos."""
    from nba_api.stats.endpoints import playergamelogs

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = playergamelogs.PlayerGameLogs(
                season_nullable=season,
                season_type_nullable=season_type,
                timeout=timeout,
            )
            raw = response.get_data_frames()[0]
            if raw.empty:
                raise RuntimeError(f"La API devolvio 0 filas para {season}")
            LOG.info("Descargados %s game logs de jugador (%s filas)", season, len(raw))
            return normalize_player_logs(raw, season)
        except Exception as exc:  # noqa: BLE001 - la API falla de formas variadas
            last_error = exc
            wait = 2 * attempt
            LOG.warning(
                "Error descargando player logs %s (intento %s/%s): %s. "
                "Reintento en %ss",
                season,
                attempt,
                retries,
                exc,
                wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"No se pudieron descargar player logs de {season}") from last_error


def load_player_logs(
    seasons: tuple[str, ...] | list[str],
    refresh: bool = False,
    pause: float = 0.8,
) -> pd.DataFrame:
    """Lee de cache o descarga las temporadas indicadas y las concatena."""
    frames: list[pd.DataFrame] = []
    for season in seasons:
        path = player_log_path(season)
        if path.exists() and not refresh:
            frame = pd.read_csv(path, dtype={"game_id": str})
            frame["game_date"] = pd.to_datetime(frame["game_date"])
            frame["game_id"] = frame["game_id"].astype(str).str.zfill(10)
        else:
            frame = fetch_player_season(season)
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False, encoding="utf-8")
            time.sleep(pause)
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=PLAYER_LOG_COLUMNS)
    return pd.concat(frames, ignore_index=True).sort_values(
        ["game_date", "game_id", "player_id"]
    ).reset_index(drop=True)

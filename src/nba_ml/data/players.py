"""Estadisticas de jugadores por temporada (puntos, rebotes y asistencias).

Descarga ``LeagueDashPlayerStats`` y cachea el resultado en
``data/raw/player_stats_<temporada>_<modo>.csv``. El modo puede ser ``PerGame``
(medias por partido) o ``Totals`` (totales de la temporada).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import RAW_DIR

PER_MODES = {"PerGame": "pergame", "Totals": "totals"}

PLAYER_COLUMNS = [
    "player_id",
    "player_name",
    "team_id",
    "team",
    "gp",
    "min",
    "pts",
    "reb",
    "ast",
    "pra",
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
    "stl",
    "blk",
    "tov",
    "pf",
    "plus_minus",
]

RENAME = {
    "PLAYER_ID": "player_id",
    "PLAYER_NAME": "player_name",
    "TEAM_ID": "team_id",
    "TEAM_ABBREVIATION": "team",
    "GP": "gp",
    "MIN": "min",
    "PTS": "pts",
    "REB": "reb",
    "AST": "ast",
    "FGM": "fgm",
    "FGA": "fga",
    "FG_PCT": "fg_pct",
    "FG3M": "fg3m",
    "FG3A": "fg3a",
    "FG3_PCT": "fg3_pct",
    "FTM": "ftm",
    "FTA": "fta",
    "FT_PCT": "ft_pct",
    "OREB": "oreb",
    "DREB": "dreb",
    "STL": "stl",
    "BLK": "blk",
    "TOV": "tov",
    "PF": "pf",
    "PLUS_MINUS": "plus_minus",
}

NUMERIC_COLUMNS = [
    "player_id",
    "team_id",
    "gp",
    "min",
    "pts",
    "reb",
    "ast",
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
    "stl",
    "blk",
    "tov",
    "pf",
    "plus_minus",
]


def player_stats_path(season: str, per_mode: str = "PerGame") -> Path:
    if per_mode not in PER_MODES:
        raise ValueError(
            f"Modo no valido: {per_mode!r}. Usa uno de {sorted(PER_MODES)}"
        )
    suffix = PER_MODES[per_mode]
    return RAW_DIR / f"player_stats_{season.replace('-', '_')}_{suffix}.csv"


def normalize_player_stats(raw: pd.DataFrame) -> pd.DataFrame:
    """Renombra columnas, calcula PRA y limpia filas sin jugador/equipo."""
    if raw.empty:
        return pd.DataFrame(columns=PLAYER_COLUMNS)

    frame = raw.rename(columns=RENAME).copy()
    required = {"player_id", "player_name", "team", "gp", "pts", "reb", "ast"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Faltan columnas en LeagueDashPlayerStats: {sorted(missing)}")

    for column in NUMERIC_COLUMNS:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame["player_name"] = frame["player_name"].fillna("").astype(str).str.strip()
    frame["team"] = frame["team"].fillna("").astype(str).str.upper().str.strip()
    frame = frame[frame["player_name"].ne("") & frame["team"].ne("")].copy()

    frame["gp"] = frame["gp"].fillna(0).astype(int)
    frame["pra"] = frame[["pts", "reb", "ast"]].sum(axis=1, min_count=3)

    columns = [column for column in PLAYER_COLUMNS if column in frame.columns]
    frame = frame.sort_values(["pra", "pts"], ascending=False).reset_index(drop=True)
    return frame[columns]


def load_player_stats(
    season: str,
    per_mode: str = "PerGame",
    refresh: bool = False,
    timeout: int = 60,
) -> pd.DataFrame:
    """Lee las estadisticas cacheadas o las descarga desde stats.nba.com."""
    path = player_stats_path(season, per_mode)
    if path.exists() and not refresh:
        return pd.read_csv(path, dtype={"player_id": int})

    from nba_api.stats.endpoints import leaguedashplayerstats

    raw = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season,
        per_mode_detailed=per_mode,
        timeout=timeout,
    ).get_data_frames()[0]
    frame = normalize_player_stats(raw)
    if frame.empty:
        raise ValueError(f"Sin estadisticas de jugadores para {season} ({per_mode})")

    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return frame

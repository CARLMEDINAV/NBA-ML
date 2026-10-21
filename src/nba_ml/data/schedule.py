"""Calendario de la temporada y seleccion de proximos partidos.

El calendario se descarga de stats.nba.com con ``ScheduleLeagueV2`` y se cachea
en ``data/raw/schedule_<temporada>.csv`` para que el dashboard no dependa de la
red en cada visita.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import RAW_DIR

# Solo temporada regular: los game_id de pretemporada empiezan por 001 y los de
# playoffs por 004.
REGULAR_SEASON_PREFIX = "002"

SCHEDULE_COLUMNS = [
    "game_id",
    "game_date",
    "game_time_et",
    "status",
    "status_text",
    "home",
    "away",
    "arena",
    "is_neutral",
]


def current_season(today: str | pd.Timestamp | None = None) -> str:
    """Temporada NBA en curso (la nueva empieza en septiembre)."""
    day = pd.Timestamp(today) if today is not None else pd.Timestamp.today()
    year = day.year if day.month >= 9 else day.year - 1
    return f"{year}-{str(year + 1)[-2:]}"


def schedule_path(season: str) -> Path:
    return RAW_DIR / f"schedule_{season.replace('-', '_')}.csv"


def normalize_schedule(raw: pd.DataFrame) -> pd.DataFrame:
    """Normaliza la respuesta de ``ScheduleLeagueV2``.

    Filtra pretemporada/playoffs, descarta fechas invalidas y deja una fila por
    partido con fecha, hora del Este y equipos.
    """
    if raw.empty:
        return pd.DataFrame(columns=SCHEDULE_COLUMNS)

    frame = raw.copy()
    game_id = frame["gameId"].astype(str)
    frame = frame[game_id.str.startswith(REGULAR_SEASON_PREFIX)].copy()
    if frame.empty:
        return pd.DataFrame(columns=SCHEDULE_COLUMNS)

    frame["game_id"] = frame["gameId"].astype(str)
    frame["game_date"] = (
        pd.to_datetime(frame["gameDateEst"], errors="coerce", utc=True)
        .dt.tz_localize(None)
        .dt.normalize()
    )
    frame["game_time_et"] = (
        pd.to_datetime(frame["gameTimeEst"], errors="coerce").dt.strftime("%H:%M")
    )
    frame["status"] = (
        pd.to_numeric(frame["gameStatus"], errors="coerce").fillna(1).astype(int)
    )
    frame["status_text"] = frame["gameStatusText"].fillna("").astype(str).str.strip()
    frame["home"] = frame["homeTeam_teamTricode"].astype(str).str.upper().str.strip()
    frame["away"] = frame["awayTeam_teamTricode"].astype(str).str.upper().str.strip()
    if "arenaName" in frame:
        frame["arena"] = frame["arenaName"].fillna("").astype(str)
    else:
        frame["arena"] = ""
    if "isNeutral" in frame:
        frame["is_neutral"] = frame["isNeutral"].fillna(False).astype(bool)
    else:
        frame["is_neutral"] = False

    frame = frame.dropna(subset=["game_date", "home", "away"])
    frame = frame[frame["home"].ne("") & frame["away"].ne("")]
    frame = frame.sort_values(
        ["game_date", "game_time_et", "game_id"], na_position="last"
    )
    return frame[SCHEDULE_COLUMNS].reset_index(drop=True)


def load_schedule(
    season: str,
    refresh: bool = False,
    timeout: int = 60,
) -> pd.DataFrame:
    """Lee el calendario cacheado o lo descarga si no existe / se pide refresco."""
    path = schedule_path(season)
    if path.exists() and not refresh:
        return pd.read_csv(
            path,
            dtype={"game_id": str},
            parse_dates=["game_date"],
        )

    # Import perezoso: el resto del proyecto no necesita nba_api para leer cache.
    from nba_api.stats.endpoints import scheduleleaguev2

    raw = scheduleleaguev2.ScheduleLeagueV2(
        season=season, timeout=timeout
    ).get_data_frames()[0]
    frame = normalize_schedule(raw)
    if frame.empty:
        raise ValueError(f"El calendario de {season} no devolvio partidos de temporada regular")

    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return frame


def upcoming_games(
    schedule: pd.DataFrame,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    only_scheduled: bool = True,
    teams: list[str] | None = None,
) -> pd.DataFrame:
    """Partidos en un rango de fechas (incluido) y, por defecto, aun no jugados."""
    if schedule.empty:
        return schedule.copy()

    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    dates = pd.to_datetime(schedule["game_date"])

    mask = dates.between(start_ts, end_ts)
    if only_scheduled:
        mask &= schedule["status"].eq(1)
    if teams:
        selected = {team.upper() for team in teams}
        mask &= schedule["home"].isin(selected) | schedule["away"].isin(selected)

    return schedule[mask].sort_values(
        ["game_date", "game_time_et", "game_id"], na_position="last"
    )

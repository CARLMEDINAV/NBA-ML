"""Descarga y cacheo de los game logs por equipo desde stats.nba.com."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from ..config import RAW_DIR, SEASON_TYPE, SEASONS, ensure_directories

LOG = logging.getLogger(__name__)

GAME_LOG_FILENAME = "team_game_logs.csv"


def _cache_path(season: str) -> Path:
    return RAW_DIR / f"team_game_logs_{season.replace('-', '_')}.csv"


def fetch_season(
    season: str,
    season_type: str = SEASON_TYPE,
    timeout: int = 30,
    retries: int = 3,
) -> pd.DataFrame:
    """Descarga una temporada, con reintentos y espera exponencial."""
    # Import perezoso: la API de Vercel solo lee artefactos y no necesita nba_api.
    from nba_api.stats.endpoints import leaguegamelog

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = leaguegamelog.LeagueGameLog(
                season=season,
                season_type_all_star=season_type,
                timeout=timeout,
            )
            frame = response.get_data_frames()[0]
            if frame.empty:
                raise RuntimeError(f"La API devolvio 0 filas para {season}")
            frame = frame.copy()
            frame["SEASON"] = season
            LOG.info("Descargada %s (%s filas)", season, len(frame))
            return frame
        except Exception as exc:  # noqa: BLE001 - la API falla de formas variadas
            last_error = exc
            wait = 2 * attempt
            LOG.warning(
                "Error descargando %s (intento %s/%s): %s. Reintento en %ss",
                season,
                attempt,
                retries,
                exc,
                wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"No se pudo descargar la temporada {season}") from last_error


def _normalize_types(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["GAME_DATE"] = pd.to_datetime(frame["GAME_DATE"])
    frame["GAME_ID"] = frame["GAME_ID"].astype(str).str.zfill(10)
    frame["TEAM_ID"] = frame["TEAM_ID"].astype(int)
    return frame


def download(
    seasons: tuple[str, ...] | list[str] = SEASONS,
    force: bool = False,
    pause: float = 0.6,
) -> pd.DataFrame:
    """Descarga (o lee de cache) las temporadas y escribe el CSV consolidado.

    Parameters
    ----------
    seasons:
        Temporadas a incluir, en formato ``"2024-25"``.
    force:
        Si es ``True`` vuelve a descargar aunque exista cache local.
    pause:
        Segundos de espera entre peticiones para no saturar la API.
    """
    ensure_directories()
    frames: dict[str, pd.DataFrame] = {}

    for season in seasons:
        cache = _cache_path(season)
        if cache.exists() and not force:
            LOG.info("Cache encontrada para %s", season)
            frame = pd.read_csv(cache, dtype={"GAME_ID": str, "SEASON_ID": str})
            frame["SEASON"] = season
        else:
            frame = fetch_season(season)
            frame.to_csv(cache, index=False, encoding="utf-8")
            time.sleep(pause)
        frames[season] = frame

    # El consolidado incluye todas las temporadas cacheadas, de modo que
    # `download --seasons 2026-27` anade la nueva sin perder el historico.
    for cache in sorted(RAW_DIR.glob("team_game_logs_*.csv")):
        season = cache.stem.removeprefix("team_game_logs_").replace("_", "-")
        if season in frames:
            continue
        frame = pd.read_csv(cache, dtype={"GAME_ID": str, "SEASON_ID": str})
        frame["SEASON"] = season
        frames[season] = frame

    data = pd.concat(list(frames.values()), ignore_index=True)
    data = _normalize_types(data)
    data = data.drop_duplicates(subset=["GAME_ID", "TEAM_ID"])
    data = data.sort_values(["GAME_DATE", "GAME_ID", "TEAM_ID"]).reset_index(drop=True)

    output = RAW_DIR / GAME_LOG_FILENAME
    data.to_csv(output, index=False, encoding="utf-8")
    LOG.info(
        "Guardadas %s filas de %s temporadas en %s",
        len(data),
        len(frames),
        output,
    )
    return data


def load_raw() -> pd.DataFrame:
    """Carga el CSV consolidado generado por :func:`download`."""
    path = RAW_DIR / GAME_LOG_FILENAME
    if not path.exists():
        raise FileNotFoundError(
            f"No existe {path}. Ejecuta primero: nba-ml download"
        )
    frame = pd.read_csv(path, dtype={"GAME_ID": str, "SEASON_ID": str})
    return _normalize_types(frame)

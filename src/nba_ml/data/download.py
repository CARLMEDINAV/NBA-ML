"""Descarga y cacheo de los game logs por equipo desde stats.nba.com."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from ..config import CURRENT_SEASON, RAW_DIR, SEASON_TYPE, SEASONS, ensure_directories

LOG = logging.getLogger(__name__)

GAME_LOG_FILENAME = "team_game_logs.csv"


def _cache_path(season: str) -> Path:
    return RAW_DIR / f"team_game_logs_{season.replace('-', '_')}.csv"


def fetch_season(
    season: str,
    season_type: str = SEASON_TYPE,
    timeout: int = 90,
    retries: int = 5,
) -> pd.DataFrame:
    """Descarga una temporada con timeout amplio y reintentos progresivos."""
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
            wait = min(60, 5 * (2 ** (attempt - 1)))
            LOG.warning(
                "Error descargando %s (intento %s/%s, timeout=%ss): %s. Reintento en %ss",
                season,
                attempt,
                retries,
                timeout,
                exc,
                wait,
            )
            if attempt < retries:
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
    """Descarga temporadas disponibles y conserva el cache de las que fallen.

    Una temporada histórica sin cache no bloquea las demás. Se vuelve a
    intentar en la siguiente ejecución. La temporada en curso se refresca
    siempre para incorporar los partidos nuevos; si falla, usa su cache.
    """
    ensure_directories()
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []

    for season in seasons:
        cache = _cache_path(season)
        refresh_current = season == CURRENT_SEASON
        if cache.exists() and not force and not refresh_current:
            LOG.info("Cache encontrada para %s", season)
            frame = pd.read_csv(cache, dtype={"GAME_ID": str, "SEASON_ID": str})
            frame["SEASON"] = season
        else:
            try:
                frame = fetch_season(season)
                frame.to_csv(cache, index=False, encoding="utf-8")
                time.sleep(pause)
            except Exception as exc:  # noqa: BLE001 - continuar con otras temporadas
                if cache.exists():
                    LOG.warning(
                        "No se pudo actualizar %s; se conserva el cache local: %s",
                        season,
                        exc,
                    )
                    frame = pd.read_csv(
                        cache, dtype={"GAME_ID": str, "SEASON_ID": str}
                    )
                    frame["SEASON"] = season
                else:
                    missing.append(season)
                    LOG.error(
                        "Temporada %s omitida; se reintentará en la próxima ejecución: %s",
                        season,
                        exc,
                    )
                    continue
        frames[season] = frame

    # Solo se incluyen las temporadas solicitadas. Esto evita que caches
    # antiguas restauradas por GitHub Actions amplien el historial sin querer.

    if not frames:
        raise RuntimeError(
            "No se pudo obtener ninguna temporada; revisa la conexión con stats.nba.com."
        )

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
    if missing:
        LOG.warning(
            "Quedaron %s temporadas sin datos (%s). El pipeline continúa y se "
            "reintentarán en la próxima ejecución.",
            len(missing),
            ", ".join(missing),
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

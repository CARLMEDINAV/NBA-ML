"""Descarga y cacheo de game logs por temporada desde stats.nba.com.

Cada temporada se guarda inmediatamente. Si la API falla, se reutiliza su
cache; si falta alguna de las temporadas solicitadas, el comando falla antes
de construir el dataset o entrenar con un historial incompleto.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from ..config import CURRENT_SEASON, RAW_DIR, SEASON_TYPE, SEASONS, ensure_directories

LOG = logging.getLogger(__name__)

GAME_LOG_FILENAME = "team_game_logs.csv"
REQUEST_TIMEOUT = 25
REQUEST_RETRIES = 2
RETRY_WAIT_SECONDS = 3


def _cache_path(season: str) -> Path:
    return RAW_DIR / f"team_game_logs_{season.replace('-', '_')}.csv"


def fetch_season(
    season: str,
    season_type: str = SEASON_TYPE,
    timeout: int = REQUEST_TIMEOUT,
    retries: int = REQUEST_RETRIES,
) -> pd.DataFrame:
    """Descarga una temporada con timeout y reintentos acotados."""
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
        except Exception as exc:  # noqa: BLE001 - la API puede fallar de varias formas
            last_error = exc
            LOG.warning(
                "Error descargando %s (intento %s/%s, timeout=%ss): %s",
                season, attempt, retries, timeout, exc,
            )
            if attempt < retries:
                LOG.info("Reintento de %s en %ss", season, RETRY_WAIT_SECONDS)
                time.sleep(RETRY_WAIT_SECONDS)
    raise RuntimeError(f"No se pudo descargar la temporada {season}") from last_error


def _normalize_types(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["GAME_DATE"] = pd.to_datetime(frame["GAME_DATE"])
    frame["GAME_ID"] = frame["GAME_ID"].astype(str).str.zfill(10)
    frame["TEAM_ID"] = frame["TEAM_ID"].astype(int)
    return frame


def _read_season_cache(cache: Path, season: str) -> pd.DataFrame:
    frame = pd.read_csv(cache, dtype={"GAME_ID": str, "SEASON_ID": str})
    if frame.empty:
        raise ValueError(f"Cache vacia para {season}: {cache}")
    required = {"GAME_ID", "TEAM_ID", "GAME_DATE", "SEASON"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Cache incompleta para {season}; faltan columnas: {sorted(missing)}")
    frame["SEASON"] = season
    return frame


def download(
    seasons: tuple[str, ...] | list[str] = SEASONS,
    force: bool = False,
    pause: float = 0.3,
) -> pd.DataFrame:
    """Obtiene las temporadas pedidas, guardando cada una inmediatamente.

    Las temporadas ya cacheadas no se vuelven a descargar, salvo la actual,
    que se refresca para incorporar partidos recientes. Si la descarga falla
    se conserva el cache de esa temporada. Nunca devuelve un dataset parcial:
    si falta una temporada, falla para impedir un entrenamiento incompleto.
    """
    started = time.monotonic()
    ensure_directories()
    requested = tuple(dict.fromkeys(seasons))
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []

    # Elimina caches antiguas que ya no forman parte de la ventana solicitada.
    requested_cache_names = {_cache_path(season).name for season in requested}
    for cache in RAW_DIR.glob("team_game_logs_*.csv"):
        if cache.name not in requested_cache_names:
            cache.unlink()
            LOG.info("Cache fuera de ventana eliminada: %s", cache.name)

    for index, season in enumerate(requested, start=1):
        cache = _cache_path(season)
        refresh_current = season == CURRENT_SEASON
        LOG.info("Temporada %s/%s: %s", index, len(requested), season)

        if cache.exists() and not force and not refresh_current:
            try:
                frame = _read_season_cache(cache, season)
                LOG.info("Cache reutilizada para %s (%s filas)", season, len(frame))
                frames[season] = frame
                continue
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Cache invalida para %s; se intentara descargar: %s", season, exc)

        try:
            frame = fetch_season(season)
            # Escritura atomica: un corte no deja un CSV de cache a medio escribir.
            temporary = cache.with_suffix(".csv.tmp")
            frame.to_csv(temporary, index=False, encoding="utf-8")
            temporary.replace(cache)
            frames[season] = frame
            LOG.info("Cache guardada para %s (%s filas)", season, len(frame))
            if pause and index < len(requested):
                time.sleep(pause)
        except Exception as exc:  # noqa: BLE001
            if cache.exists():
                try:
                    frame = _read_season_cache(cache, season)
                    frames[season] = frame
                    LOG.warning("Se conserva cache previa de %s: %s", season, exc)
                    continue
                except Exception as cache_exc:  # noqa: BLE001
                    LOG.error("Cache de %s no utilizable: %s", season, cache_exc)
            missing.append(season)
            LOG.error("No hay datos utilizables para %s: %s", season, exc)

    if missing:
        elapsed = time.monotonic() - started
        raise RuntimeError(
            f"Descarga incompleta en {elapsed:.1f}s. Faltan temporadas: "
            f"{', '.join(missing)}. Se conservaron las caches de las temporadas "
            "descargadas; vuelve a ejecutar para reintentar solo las faltantes."
        )

    if len(frames) != len(requested):
        raise RuntimeError(
            f"Se esperaban {len(requested)} temporadas y se obtuvieron {len(frames)}."
        )

    data = pd.concat([frames[season] for season in requested], ignore_index=True)
    data = _normalize_types(data)
    data = data.drop_duplicates(subset=["GAME_ID", "TEAM_ID"])
    data = data.sort_values(["GAME_DATE", "GAME_ID", "TEAM_ID"]).reset_index(drop=True)

    output = RAW_DIR / GAME_LOG_FILENAME
    data.to_csv(output, index=False, encoding="utf-8")
    elapsed = time.monotonic() - started
    LOG.info(
        "Descarga completa: %s filas, %s/%s temporadas, %.1fs. Temporadas: %s",
        len(data), len(frames), len(requested), elapsed, ", ".join(requested),
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

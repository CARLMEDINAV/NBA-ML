"""API FastAPI y frontend publico para Vercel.

La app de Streamlit sigue siendo el dashboard completo para uso local. Esta
version es la que se despliega en Vercel: FastAPI sirve un frontend estatico y
lee una foto de los artefactos incluida en ``artifacts/`` (modelo, estado de
equipos, calendario, estadisticas de jugadores y predicciones de test).

Ejecutar en local:
    uvicorn app:app --reload --port 8000
"""

from __future__ import annotations

import math
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"
PUBLIC_DIR = ROOT / "public"

# Vercel no instala el paquete editable; con esto basta para importar src/.
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from nba_ml.betting import (
    american_to_decimal,
    combine_independent,
    expected_value,
    fair_decimal_odds,
    handicap_side_probability,
    potential_return,
    team_total_side_probability,
    total_side_probability,
    validate_decimal_odds,
    validate_probability,
    validate_stake,
    winner_side_probability,
)
from nba_ml.models.player_props import (
    load_player_props_artifact,
    load_player_state,
    predict_player_props,
)
from nba_ml.models.predict import load_artifact, load_team_state, predict_game
from nba_ml.models.results import add_over_under, summarize_results

AVAILABLE_SEASONS = ("2025-26", "2026-27")
PLAYER_MODES = {"pergame": "pergame", "totals": "totals"}
SORT_COLUMNS = {"pra", "pts", "reb", "ast", "min", "gp"}
PLAYER_HISTORY_COLUMNS = [
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
PLAYER_DEFENDER_COLUMNS = [
    "season",
    "off_player_id",
    "def_player_id",
    "def_player_name",
    "def_team",
    "gp",
    "matchup_min",
    "player_pts",
    "matchup_ast",
    "matchup_tov",
    "matchup_blk",
    "matchup_fgm",
    "matchup_fga",
    "matchup_fg3m",
    "matchup_fg3a",
    "matchup_ftm",
    "matchup_fta",
]
LEAN_OVER = 0.55
LEAN_UNDER = 0.45
NBA_ET = ZoneInfo("America/New_York")
NBA_STATS_SCOREBOARD = "https://stats.nba.com/stats/scoreboardv3"
LIVE_TIMEOUT = 9.0
LIVE_CACHE_SECONDS = 25
LIVE_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
ODDS_CACHE_SECONDS = 300
ODDS_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
MAX_ODDS_DATES = 8
ESPN_INJURIES_URL = (
    "https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
)
ESPN_INJURIES_PAGE = "https://www.espn.com/nba/injuries"
INJURY_CACHE_SECONDS = 300
INJURY_CACHE: dict[str, Any] = {}
INJURY_STATUS_LABELS = {
    "out": "Baja",
    "day-to-day": "Día a día",
    "day to day": "Día a día",
    "doubtful": "Duda alta",
    "questionable": "Duda",
    "probable": "Probable",
    "available": "Disponible",
}
INJURY_MODEL_NOTE = (
    "El modelo Ridge y las probabilidades de Bet Builder no incorporan lesiones, "
    "alineaciones ni minutos disponibles. Este parte es contexto: no resta puntos "
    "ni cambia automáticamente una proyección. Un ajuste de impacto requeriría un "
    "modelo calibrado de disponibilidad y roles, que este proyecto no tiene."
)
INJURY_IMPACT_NOTE = (
    "Para orientar el impacto mostramos los puntos, rebotes, asistencias y PRA por "
    "partido del jugador y su posición dentro del equipo. No se presenta una "
    "pérdida de puntos inventada ni una probabilidad ajustada."
)
INJURY_LIMITATIONS = [
    "ESPN publica el estado y una nota periodística; no garantiza que la baja se "
    "mantenga hasta el partido ni sustituye un reporte oficial de la NBA.",
    "El ranking interno usa las medias por partido de 2026-27 y puede estar "
    "afectado por muestras cortas, traspasos o jugadores sin minutos.",
    "El modelo de totales, hándicap, ganador y Bet Builder no usa lesiones. Las "
    "probabilidades mostradas no descuentan al jugador ausente.",
]
ESPN_TO_NBA = {
    "GS": "GSW",
    "NO": "NOP",
    "NY": "NYK",
    "SA": "SAS",
    "UTAH": "UTA",
    "WSH": "WAS",
}


class BetBuilderSelectionIn(BaseModel):
    event_id: str = Field(..., min_length=1, max_length=64)
    market: str = Field(..., min_length=1, max_length=32)
    side: str = Field(..., min_length=1, max_length=8)
    line: float | None = Field(None, ge=-100, le=400)
    event_date: str | None = Field(None, max_length=10)


class BetBuilderAnalyzeIn(BaseModel):
    selections: list[BetBuilderSelectionIn] = Field(default_factory=list, max_length=20)
    stake: float = Field(10.0, gt=0, le=1_000_000.0)
    decimal_odds: float | None = Field(None, gt=1.0, le=1000.0)


app = FastAPI(
    title="NBA ML API",
    description=(
        "Prediccion de totales, handicap y props de jugadores "
        "(PTS, REB, AST y PRA)."
    ),
    version="0.6.1",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Carga de artefactos
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _artifact_cached() -> dict:
    return load_artifact(ARTIFACTS / "model.joblib")


@lru_cache(maxsize=1)
def _state_cached() -> pd.DataFrame:
    return load_team_state(ARTIFACTS / "team_state.csv")


@lru_cache(maxsize=1)
def _schedule_cached() -> pd.DataFrame:
    return pd.read_csv(
        ARTIFACTS / "schedule_2026_27.csv",
        dtype={"game_id": str},
        parse_dates=["game_date"],
    )


@lru_cache(maxsize=1)
def _test_predictions_cached() -> pd.DataFrame:
    return pd.read_csv(
        ARTIFACTS / "test_predictions.csv",
        dtype={"game_id": str},
        parse_dates=["game_date"],
    )


@lru_cache(maxsize=8)
def _players_cached(season: str, mode: str) -> pd.DataFrame:
    suffix = PLAYER_MODES.get(mode)
    if suffix is None:
        raise ValueError("Modo de jugadores no valido")
    path = ARTIFACTS / f"player_stats_{season.replace('-', '_')}_{suffix}.csv"
    return pd.read_csv(path, dtype={"player_id": int})


@lru_cache(maxsize=1)
def _player_props_cached() -> dict:
    return load_player_props_artifact(ARTIFACTS / "player_props.joblib")


@lru_cache(maxsize=1)
def _player_state_cached() -> pd.DataFrame:
    return load_player_state(ARTIFACTS / "player_state.csv")


@lru_cache(maxsize=1)
def _player_history_cached() -> pd.DataFrame:
    path = ARTIFACTS / "player_game_logs_recent.csv"
    if not path.exists():
        return pd.DataFrame(columns=PLAYER_HISTORY_COLUMNS)
    return pd.read_csv(
        path,
        dtype={"player_id": int, "game_id": str},
        parse_dates=["game_date"],
    )


@lru_cache(maxsize=1)
def _player_defenders_cached() -> pd.DataFrame:
    path = ARTIFACTS / "player_defenders_recent.csv"
    if not path.exists():
        return pd.DataFrame(columns=PLAYER_DEFENDER_COLUMNS)
    return pd.read_csv(
        path,
        dtype={"off_player_id": int, "def_player_id": int},
    )


@lru_cache(maxsize=1)
def _player_bio_cached() -> dict[int, dict[str, Any]]:
    path = ARTIFACTS / "player_bio.csv"
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    return {
        int(row.player_id): {
            "player_name": str(row.player_name),
            "team": str(row.team).upper().strip(),
            "height_inches": _rounded(row.height_inches, 1),
            "weight": _rounded(row.weight, 1),
        }
        for row in frame.itertuples(index=False)
        if not pd.isna(row.player_id)
    }


def _artifact() -> dict:
    try:
        return _artifact_cached()
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _state() -> pd.DataFrame:
    try:
        return _state_cached()
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _schedule() -> pd.DataFrame:
    try:
        return _schedule_cached().copy()
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _test_predictions() -> pd.DataFrame:
    try:
        return _test_predictions_cached().copy()
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _players(season: str, mode: str) -> pd.DataFrame:
    try:
        return _players_cached(season, mode).copy()
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=f"No hay estadisticas de jugadores para {season} ({mode})",
        ) from error


def _player_props() -> dict:
    try:
        return _player_props_cached()
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _player_state() -> pd.DataFrame:
    try:
        return _player_state_cached().copy()
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _player_history() -> pd.DataFrame:
    return _player_history_cached().copy()


def _player_defenders() -> pd.DataFrame:
    return _player_defenders_cached().copy()


def _player_bio() -> dict[int, dict[str, Any]]:
    return _player_bio_cached()


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _clean(value: Any) -> Any:
    """Convierte tipos de pandas/numpy a JSON estricto (sin NaN)."""
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, np.ndarray):
        return [_clean(item) for item in value.tolist()]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _ok(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(content=_clean(payload))


def _today(value: str | None = None) -> pd.Timestamp:
    if not value:
        return _et_today()
    try:
        return pd.Timestamp(value).normalize()
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=400, detail="Fecha no valida; usa YYYY-MM-DD"
        ) from error


def _season_for(day: pd.Timestamp) -> str:
    year = day.year if day.month >= 9 else day.year - 1
    return f"{year}-{str(year + 1)[-2:]}"


def _et_today() -> pd.Timestamp:
    """Fecha NBA: usa hora del Este, no la del servidor (Vercel corre en UTC)."""
    return pd.Timestamp.now(tz=NBA_ET).normalize().tz_localize(None)


def _nba_abbreviation(value: str) -> str:
    text = str(value or "").upper().strip()
    return ESPN_TO_NBA.get(text, text)


def _fetch_espn_scoreboard(
    day: pd.Timestamp, schedule: pd.DataFrame
) -> pd.DataFrame:
    """Marcadores de ESPN; se usan solo para actualizar la tabla de resultados."""
    columns = [
        "game_id",
        "status",
        "status_text",
        "home_score",
        "away_score",
        "total_points",
    ]
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    try:
        response = httpx.get(
            url,
            params={"dates": day.strftime("%Y%m%d")},
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; nba-ml/0.2)",
                "Accept": "application/json",
            },
            timeout=LIVE_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
        events = response.json().get("events", [])
    except Exception:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, Any]] = []
    for event in events:
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competitors = competitions[0].get("competitors") or []
        home = next(
            (item for item in competitors if item.get("homeAway") == "home"), None
        )
        away = next(
            (item for item in competitors if item.get("homeAway") == "away"), None
        )
        if not home or not away:
            continue
        home_team = _nba_abbreviation(home.get("team", {}).get("abbreviation"))
        away_team = _nba_abbreviation(away.get("team", {}).get("abbreviation"))
        try:
            event_day = (
                pd.Timestamp(event["date"]).tz_convert(NBA_ET).normalize().tz_localize(None)
            )
        except Exception:
            continue
        candidates = schedule[
            schedule["game_date"].eq(event_day)
            & schedule["home"].eq(home_team)
            & schedule["away"].eq(away_team)
        ]
        if candidates.empty:
            continue

        status_info = event.get("status", {}).get("type", {})
        state = str(status_info.get("state") or "")
        completed = bool(status_info.get("completed"))
        status = 3 if completed or state == "post" else (2 if state == "in" else 1)
        home_score = pd.to_numeric(home.get("score"), errors="coerce")
        away_score = pd.to_numeric(away.get("score"), errors="coerce")
        total = None
        if status == 3 and pd.notna(home_score) and pd.notna(away_score):
            total = float(home_score) + float(away_score)
        rows.append(
            {
                "game_id": str(candidates.iloc[0]["game_id"]),
                "status": status,
                "status_text": str(
                    status_info.get("shortDetail") or status_info.get("description") or ""
                ).strip(),
                "home_score": home_score,
                "away_score": away_score,
                "total_points": total,
            }
        )

    return pd.DataFrame(rows, columns=columns)


def _fetch_nba_scoreboard(day: pd.Timestamp) -> pd.DataFrame:
    """Resultados en vivo de stats.nba.com para una fecha NBA (sin nba_api)."""
    columns = [
        "game_id",
        "status",
        "status_text",
        "home_score",
        "away_score",
        "total_points",
    ]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nba.com/",
        "Origin": "https://www.nba.com",
    }
    params = {"GameDate": day.strftime("%m/%d/%Y"), "LeagueID": "00"}

    try:
        response = httpx.get(
            NBA_STATS_SCOREBOARD,
            params=params,
            headers=headers,
            timeout=LIVE_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
        games = response.json()["scoreboard"]["games"]
    except Exception:
        # La web debe seguir funcionando con el calendario cacheado si la NBA falla.
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, Any]] = []
    for game in games:
        status = int(game.get("gameStatus") or 1)
        home_score = pd.to_numeric(game.get("homeTeam", {}).get("score"), errors="coerce")
        away_score = pd.to_numeric(game.get("awayTeam", {}).get("score"), errors="coerce")
        total = None
        if status == 3 and pd.notna(home_score) and pd.notna(away_score):
            total = float(home_score) + float(away_score)
        rows.append(
            {
                "game_id": str(game.get("gameId")),
                "status": status,
                "status_text": str(game.get("gameStatusText") or "").strip(),
                "home_score": home_score,
                "away_score": away_score,
                "total_points": total,
            }
        )

    return pd.DataFrame(rows, columns=columns)


def _fetch_scoreboard(day: pd.Timestamp, schedule: pd.DataFrame) -> pd.DataFrame:
    """ESPN primero (Vercel lo alcanza de forma fiable) y stats.nba.com después."""
    key = day.strftime("%Y-%m-%d")
    cached = LIVE_CACHE.get(key)
    if cached and time.time() - cached[0] < LIVE_CACHE_SECONDS:
        return cached[1].copy()

    frame = _fetch_espn_scoreboard(day, schedule)
    if frame.empty:
        frame = _fetch_nba_scoreboard(day)
    LIVE_CACHE[key] = (time.time(), frame.copy())
    return frame


def _live_dates(date: str | None = None) -> list[pd.Timestamp]:
    if date:
        return [_today(date)]
    today = _et_today()
    # Incluye el dia anterior: los partidos del Oeste pueden acabar tras medianoche ET.
    return [today - pd.Timedelta(days=1), today]


def _merge_live(
    schedule: pd.DataFrame, dates: list[pd.Timestamp]
) -> tuple[pd.DataFrame, str | None]:
    frames = [_fetch_scoreboard(day, schedule) for day in dates]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return schedule, None

    live = pd.concat(frames, ignore_index=True)
    live = live.drop_duplicates(subset=["game_id"], keep="last").set_index("game_id")
    merged = schedule.copy()
    for column in ["status", "status_text", "home_score", "away_score", "total_points"]:
        if column not in live.columns:
            continue
        mapped = merged["game_id"].map(live[column])
        merged[column] = mapped.where(mapped.notna(), merged[column])
    updated = pd.Timestamp.now(tz=NBA_ET).strftime("%Y-%m-%d %H:%M ET")
    return merged, updated


def _team_names(state: pd.DataFrame) -> dict[str, str]:
    return {
        str(row.team_abbreviation): str(row.team_name)
        for row in state.itertuples(index=False)
    }


def _team_ids(state: pd.DataFrame) -> dict[str, int]:
    return {
        str(row.team_abbreviation): int(row.team_id)
        for row in state.itertuples(index=False)
        if not pd.isna(row.team_id)
    }


def _team_logo_url(team_id: Any) -> str | None:
    if team_id is None or pd.isna(team_id):
        return None
    return f"https://cdn.nba.com/logos/nba/{int(team_id)}/global/L/logo.svg"


def _rounded(value: Any, digits: int = 1) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _mean_value(frame: pd.DataFrame, column: str, digits: int = 3) -> float | None:
    if frame is None or frame.empty or column not in frame.columns:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return None
    return _rounded(values.mean(), digits)


def _sum_value(frame: pd.DataFrame, column: str) -> float:
    if frame is None or frame.empty or column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())


def _shooting_from_box(frame: pd.DataFrame) -> dict[str, Any]:
    """Efectividad agregada a partir de tiros convertidos/intentados."""
    fgm = _sum_value(frame, "fgm")
    fga = _sum_value(frame, "fga")
    fg3m = _sum_value(frame, "fg3m")
    fg3a = _sum_value(frame, "fg3a")
    ftm = _sum_value(frame, "ftm")
    fta = _sum_value(frame, "fta")
    pts = _sum_value(frame, "pts")
    return {
        "fgm": _rounded(fgm),
        "fga": _rounded(fga),
        "fg_pct": _rounded(fgm / fga, 3) if fga else None,
        "fg3m": _rounded(fg3m),
        "fg3a": _rounded(fg3a),
        "fg3_pct": _rounded(fg3m / fg3a, 3) if fg3a else None,
        "ftm": _rounded(ftm),
        "fta": _rounded(fta),
        "ft_pct": _rounded(ftm / fta, 3) if fta else None,
        "efg_pct": _rounded((fgm + 0.5 * fg3m) / fga, 3) if fga else None,
        "ts_pct": _rounded(
            pts / (2 * (fga + 0.44 * fta)),
            3,
        )
        if fga or fta
        else None,
    }


def _history_game_payload(row: Any, team_ids: dict[str, int]) -> dict[str, Any]:
    opponent = str(row.opponent).upper().strip()
    row_team = str(row.team).upper().strip()
    return {
        "game_id": str(row.game_id),
        "season": str(row.season),
        "date": pd.Timestamp(row.game_date).strftime("%Y-%m-%d"),
        "team": row_team,
        "team_logo_url": _team_logo_url(team_ids.get(row_team)),
        "opponent": opponent,
        "opponent_logo_url": _team_logo_url(team_ids.get(opponent)),
        "is_home": bool(row.is_home),
        "wl": None if pd.isna(row.wl) else str(row.wl),
        "min": _rounded(row.min),
        "pts": _rounded(row.pts),
        "reb": _rounded(row.reb),
        "ast": _rounded(row.ast),
        "stl": _rounded(row.stl),
        "blk": _rounded(row.blk),
        "fgm": _rounded(getattr(row, "fgm", None)),
        "fga": _rounded(getattr(row, "fga", None)),
        "fg_pct": _rounded(getattr(row, "fg_pct", None), 3),
        "fg3m": _rounded(getattr(row, "fg3m", None)),
        "fg3a": _rounded(getattr(row, "fg3a", None)),
        "fg3_pct": _rounded(getattr(row, "fg3_pct", None), 3),
        "ftm": _rounded(getattr(row, "ftm", None)),
        "fta": _rounded(getattr(row, "fta", None)),
        "ft_pct": _rounded(getattr(row, "ft_pct", None), 3),
        "efg_pct": _rounded(getattr(row, "efg_pct", None), 3),
        "ts_pct": _rounded(getattr(row, "ts_pct", None), 3),
        "usage": _rounded(getattr(row, "usage", None), 3),
        "tov": _rounded(row.tov),
        "pra": _rounded(row.pra),
    }


def _row_value(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, pd.Series):
        return row.get(key)
    return getattr(row, key, None)


def _role_group(
    reb: Any,
    ast: Any,
    fg3a: Any,
    height_inches: Any = None,
) -> str | None:
    """Agrupa por altura; si falta, usa el perfil estadistico."""
    height = _rounded(height_inches, 1)
    if height is not None and height > 0:
        if height >= 82:
            return "big"
        if height <= 77:
            return "guard"
        return "wing"
    rebounds = _rounded(reb, 3)
    assists = _rounded(ast, 3)
    threes = _rounded(fg3a, 3)
    if rebounds is None:
        return None
    if rebounds >= 7.0 and (threes is None or threes <= 3.5):
        return "big"
    if (assists is not None and assists >= 5.0) or (
        threes is not None and threes >= 5.0 and rebounds < 5.5
    ):
        return "guard"
    return "wing"


def _season_profile(
    row: Any | None,
    bio: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    gp = _row_value(row, "gp")
    player_id = _row_value(row, "player_id")
    height = None
    if bio is not None and player_id is not None and not pd.isna(player_id):
        height = bio.get(int(player_id), {}).get("height_inches")
    return {
        "gp": int(gp) if gp is not None and not pd.isna(gp) else None,
        "min": _rounded(_row_value(row, "min")),
        "pts": _rounded(_row_value(row, "pts")),
        "reb": _rounded(_row_value(row, "reb")),
        "ast": _rounded(_row_value(row, "ast")),
        "stl": _rounded(_row_value(row, "stl")),
        "blk": _rounded(_row_value(row, "blk")),
        "role": _role_group(
            _row_value(row, "reb"),
            _row_value(row, "ast"),
            _row_value(row, "fg3a"),
            height,
        ),
    }


def _likely_defender_payload(
    player_id: int,
    opponent: str,
    defender_groups: pd.DataFrame,
    pergame: pd.DataFrame,
    target_row: Any | None,
    team_names: dict[str, str],
    team_ids: dict[str, int],
    bio: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    """Posible defensor del proximo rival, sin presentarlo como asignacion oficial."""
    opponent = opponent.upper().strip()
    season_rows = {
        int(row.player_id): row
        for row in pergame.itertuples(index=False)
        if not pd.isna(row.player_id)
    }

    if not defender_groups.empty and "team" in defender_groups.columns:
        candidates = defender_groups[
            defender_groups["team"]
            .astype(str)
            .str.upper()
            .str.strip()
            .eq(opponent)
        ].copy()
        if not candidates.empty:
            candidate = candidates.sort_values(
                "matchup_min", ascending=False
            ).iloc[0]
            defender_id = int(candidate["def_player_id"])
            profile = _season_profile(season_rows.get(defender_id), bio)
            matched_games = max(1.0, float(candidate["gp"]))
            fga = float(candidate["matchup_fga"])
            matchup_min = float(candidate["matchup_min"])
            confidence = (
                "media"
                if matchup_min >= 10
                and (profile["min"] is None or profile["min"] >= 12)
                else "baja"
            )
            return {
                "player_id": defender_id,
                "player_name": str(candidate["def_player_name"]),
                "team": opponent,
                "team_name": team_names.get(opponent, opponent),
                "team_logo_url": _team_logo_url(team_ids.get(opponent)),
                "headshot_url": (
                    "https://cdn.nba.com/headshots/nba/latest/260x190/"
                    f"{defender_id}.png"
                ),
                "basis": "historial",
                "confidence": confidence,
                "is_historical": True,
                "matchup_gp": int(candidate["gp"]),
                "matchup_min": _rounded(matchup_min, 1),
                "matchup_min_pg": _rounded(
                    matchup_min / matched_games, 1
                ),
                "player_pts_pg": _rounded(
                    float(candidate["player_pts"]) / matched_games, 1
                ),
                "fg_pct": _rounded(
                    float(candidate["matchup_fgm"]) / fga, 3
                )
                if fga
                else None,
                **profile,
            }

    if pergame.empty:
        return None
    roster = pergame[
        pergame["team"].astype(str).str.upper().str.strip().eq(opponent)
    ].copy()
    if roster.empty:
        return None
    roster["_gp"] = pd.to_numeric(roster["gp"], errors="coerce").fillna(0)
    roster["_min"] = pd.to_numeric(roster["min"], errors="coerce").fillna(0)
    roster = roster[(roster["_gp"] >= 1) & (roster["_min"] >= 10)].copy()
    if roster.empty:
        return None
    target_role = _role_group(
        _row_value(target_row, "reb"),
        _row_value(target_row, "ast"),
        _row_value(target_row, "fg3a"),
        bio.get(player_id, {}).get("height_inches"),
    )
    roster["_role"] = [
        _role_group(
            row.reb,
            row.ast,
            row.fg3a,
            bio.get(int(row.player_id), {}).get("height_inches"),
        )
        for row in roster.itertuples(index=False)
    ]
    if target_role is not None:
        same_role = roster[roster["_role"].eq(target_role)]
        if not same_role.empty:
            roster = same_role
    row = roster.sort_values("_min", ascending=False).iloc[0]
    profile = _season_profile(row, bio)
    return {
        "player_id": int(row.player_id),
        "player_name": str(row.player_name),
        "team": opponent,
        "team_name": team_names.get(opponent, opponent),
        "team_logo_url": _team_logo_url(team_ids.get(opponent)),
        "headshot_url": (
            "https://cdn.nba.com/headshots/nba/latest/260x190/"
            f"{int(row.player_id)}.png"
        ),
        "basis": "rol",
        "confidence": "baja",
        "is_historical": False,
        "matchup_gp": None,
        "matchup_min": None,
        "matchup_min_pg": None,
        "player_pts_pg": None,
        "fg_pct": None,
        **profile,
    }


def _espn_price(block: dict[str, Any] | None) -> Any:
    """Precio americano de un bloque de cuota de ESPN (cierre, apertura o actual)."""
    if not isinstance(block, dict) or not block:
        return None
    for key in ("close", "open"):
        nested = block.get(key) or {}
        if isinstance(nested, dict) and nested.get("odds") is not None:
            return nested.get("odds")
    return block.get("odds")


def _fetch_espn_odds(day: pd.Timestamp, schedule: pd.DataFrame) -> pd.DataFrame:
    """Hándicap y cuotas de total de ESPN para una fecha concreta."""
    columns = [
        "game_id",
        "market_spread",
        "market_total",
        "market_details",
        "market_provider",
        "over_odds",
        "under_odds",
    ]
    key = f"odds:{day:%Y-%m-%d}"
    cached = ODDS_CACHE.get(key)
    if cached and time.time() - cached[0] < ODDS_CACHE_SECONDS:
        return cached[1].copy()

    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    try:
        response = httpx.get(
            url,
            params={"dates": day.strftime("%Y%m%d")},
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; nba-ml/0.3)",
                "Accept": "application/json",
            },
            timeout=LIVE_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
        events = response.json().get("events", [])
    except Exception:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, Any]] = []
    for event in events:
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competition = competitions[0]
        competitors = competition.get("competitors") or []
        home = next(
            (item for item in competitors if item.get("homeAway") == "home"), None
        )
        away = next(
            (item for item in competitors if item.get("homeAway") == "away"), None
        )
        if not home or not away:
            continue
        home_team = _nba_abbreviation(home.get("team", {}).get("abbreviation"))
        away_team = _nba_abbreviation(away.get("team", {}).get("abbreviation"))
        try:
            event_day = (
                pd.Timestamp(event["date"]).tz_convert(NBA_ET).normalize().tz_localize(None)
            )
        except Exception:
            continue
        candidates = schedule[
            schedule["game_date"].eq(event_day)
            & schedule["home"].eq(home_team)
            & schedule["away"].eq(away_team)
        ]
        if candidates.empty:
            continue

        odds_list = competition.get("odds") or []
        selected = next(
            (
                item
                for item in odds_list
                if item.get("spread") is not None or item.get("overUnder") is not None
            ),
            None,
        )
        if selected is None:
            continue
        total_block = selected.get("total") or {}
        over_block = total_block.get("over") or {}
        under_block = total_block.get("under") or {}
        rows.append(
            {
                "game_id": str(candidates.iloc[0]["game_id"]),
                "market_spread": pd.to_numeric(
                    selected.get("spread"), errors="coerce"
                ),
                "market_total": pd.to_numeric(
                    selected.get("overUnder"), errors="coerce"
                ),
                "market_details": selected.get("details"),
                "market_provider": (selected.get("provider") or {}).get("displayName"),
                "over_odds": _espn_price(over_block),
                "under_odds": _espn_price(under_block),
            }
        )

    frame = pd.DataFrame(rows, columns=columns)
    ODDS_CACHE[key] = (time.time(), frame.copy())
    return frame


def _odds_for_dates(
    dates: list[pd.Timestamp], schedule: pd.DataFrame
) -> pd.DataFrame:
    """Descarga cuotas de varias fechas en paralelo (máx. 8 fechas)."""
    unique: list[pd.Timestamp] = []
    for value in dates:
        day = pd.Timestamp(value).normalize()
        if day not in unique:
            unique.append(day)
    unique = sorted(unique)[:MAX_ODDS_DATES]
    if not unique:
        return pd.DataFrame()

    workers = min(6, len(unique))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        frames = list(pool.map(lambda day: _fetch_espn_odds(day, schedule), unique))
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    return (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=["game_id"], keep="last")
        .reset_index(drop=True)
    )


def _total_label(lean: str, line: float) -> str:
    if lean == "OVER":
        return f"▲ Más de {line:.1f} (Alta)"
    if lean == "UNDER":
        return f"▼ Menos de {line:.1f} (Baja)"
    return f"Sin señal · línea {line:.1f}"


def _directional_side(probability: float | None) -> str:
    if probability is None:
        return "—"
    if probability > 0.5:
        return "OVER"
    if probability < 0.5:
        return "UNDER"
    return "PUSH"


def _directional_label(probability: float | None, line: float) -> str:
    """Etiqueta direccional para la línea de mercado.

    Alta/Baja = señal fuerte (≥55% / ≤45%); Leve = 50-55%. Así la tabla
    muestra una dirección cuando el mercado está prácticamente equilibrado,
    sin ocultar que la ventaja estimada es pequeña.
    """
    if probability is None:
        return f"Sin cuota · línea {line:.1f}"
    if probability >= LEAN_OVER:
        return f"▲ Más de {line:.1f} (Alta)"
    if probability > 0.5:
        return f"▲ Más de {line:.1f} (Leve)"
    if probability <= LEAN_UNDER:
        return f"▼ Menos de {line:.1f} (Baja)"
    if probability < 0.5:
        return f"▼ Menos de {line:.1f} (Leve)"
    return f"Pick'em · {line:.1f}"


def _handicap_fields(
    result: dict[str, Any],
    home_name: str,
    away_name: str,
    market: dict[str, Any] | pd.Series | None,
) -> dict[str, Any]:
    margin = result.get("predicted_margin")
    if margin is None or pd.isna(margin):
        return {
            "possible_winner": None,
            "model_handicap": None,
            "model_handicap_line": None,
            "model_handicap_favorite": None,
            "market_spread": None,
            "market_handicap": None,
            "handicap_edge": None,
            "handicap_pick": None,
            "home_cover_probability": None,
            "away_cover_probability": None,
        }

    margin = float(margin)
    favorite = home_name if margin >= 0 else away_name
    line = round(abs(margin) * 2) / 2
    if line < 0.5:
        line = 0.0
    suggested = f"{favorite} -{line:.1f}" if line > 0 else "Pick'em"

    market_spread = market.get("market_spread") if market is not None else None
    if market_spread is not None and pd.isna(market_spread):
        market_spread = None
    if market_spread is not None:
        market_spread = float(market_spread)
    market_details = market.get("market_details") if market is not None else None
    market_handicap = None
    if market_spread is not None:
        if market_spread < 0:
            market_handicap = f"{home_name} -{abs(market_spread):.1f}"
        elif market_spread > 0:
            market_handicap = f"{away_name} -{abs(market_spread):.1f}"
        else:
            market_handicap = "Pick'em"
    if not market_handicap:
        market_handicap = market_details

    model_home_spread = result.get("model_home_spread")
    edge = None
    if market_spread is not None and model_home_spread is not None:
        edge = float(model_home_spread) - market_spread

    home_cover = result.get("home_cover_probability")
    away_cover = result.get("away_cover_probability")
    pick = None
    if home_cover is not None:
        home_cover = float(home_cover)
        away_cover = float(away_cover) if away_cover is not None else 1.0 - home_cover
        if home_cover >= LEAN_OVER:
            pick = home_name
        elif home_cover <= LEAN_UNDER:
            pick = away_name
        else:
            pick = "Sin señal"

    return {
        "possible_winner": favorite,
        "model_handicap": suggested,
        "model_handicap_line": line,
        "model_handicap_favorite": favorite,
        "market_spread": market_spread,
        "market_handicap": market_handicap,
        "handicap_edge": edge,
        "handicap_pick": pick,
        "home_cover_probability": home_cover,
        "away_cover_probability": away_cover,
    }


def _analysis_text(
    result: dict[str, Any],
    home_name: str,
    away_name: str,
    line: float,
    market: dict[str, Any] | pd.Series | None = None,
    market_probability: float | None = None,
) -> str:
    predicted = float(result["predicted_total"])
    probability = (
        float(result["probability_over"])
        if result.get("probability_over") is not None
        else 0.5
    )
    lean = _lean(probability)
    difference = predicted - line
    if lean == "OVER":
        total_text = (
            f"El modelo proyecta {predicted:.1f} puntos, {difference:+.1f} frente "
            f"a la línea {line:.1f}; P(over) {probability:.0%} y señal OVER."
        )
    elif lean == "UNDER":
        total_text = (
            f"El modelo proyecta {predicted:.1f} puntos, {difference:+.1f} frente "
            f"a la línea {line:.1f}; P(over) {probability:.0%} y señal UNDER."
        )
    else:
        total_text = (
            f"El modelo proyecta {predicted:.1f} puntos, {difference:+.1f} frente "
            f"a la línea {line:.1f}; P(over) {probability:.0%}, sin señal clara."
        )

    parts = [total_text]
    margin = result.get("predicted_margin")
    if margin is not None and not pd.isna(margin):
        margin = float(margin)
        favorite = home_name if margin >= 0 else away_name
        handicap_line = round(abs(margin) * 2) / 2
        suggested = (
            f"{favorite} -{handicap_line:.1f}"
            if handicap_line >= 0.5
            else "Pick'em"
        )
        parts.append(
            f"Margen previsto {home_name} {margin:+.1f}; hándicap sugerido "
            f"{suggested}."
        )

    if market is not None:
        market_spread = market.get("market_spread")
        market_total = market.get("market_total")
        details = market.get("market_details")
        market_bits: list[str] = []
        if market_spread is not None and not pd.isna(market_spread):
            market_spread = float(market_spread)
            market_bits.append(f"hándicap {details or f'{market_spread:+.1f}'}")
            model_spread = result.get("model_home_spread")
            if model_spread is not None:
                edge = float(model_spread) - market_spread
                side = home_name if edge < 0 else away_name
                market_bits.append(
                    f"el modelo está {abs(edge):.1f} puntos más alto en {side}"
                )
        if market_total is not None and not pd.isna(market_total):
            total_bit = f"total {float(market_total):.1f}"
            if market_probability is not None:
                total_bit += f" (P(over) {market_probability:.0%})"
            market_bits.append(total_bit)
        if market_bits:
            parts.append("Mercado: " + " · ".join(market_bits) + ".")

    features = result.get("features") or {}
    context: list[str] = []
    if "pace_sum" in features:
        context.append(f"ritmo combinado {float(features['pace_sum']):.1f}")
    if "off_rtg_sum" in features:
        context.append(f"rating ofensivo {float(features['off_rtg_sum']):.1f}")
    if "def_rtg_sum" in features:
        context.append(f"rating defensivo {float(features['def_rtg_sum']):.1f}")
    if context:
        parts.append("Contexto: " + ", ".join(context) + ".")
    if result.get("home_elo") is not None:
        parts.append(
            f"Elo: {home_name} {float(result['home_elo']):.0f} vs "
            f"{away_name} {float(result['away_elo']):.0f}."
        )
    if result.get("home_rest") is not None:
        rest = (
            f"Descanso: {home_name} {float(result['home_rest']):.0f}d, "
            f"{away_name} {float(result['away_rest']):.0f}d."
        )
        if (result.get("features") or {}).get("b2b_sum", 0) >= 1:
            rest += " Hay al menos un equipo en back-to-back."
        parts.append(rest)

    parts.append(
        "Riesgo: no incluye lesiones, alineaciones ni cambios de última hora; "
        "el total tiene un componente aleatorio alto."
    )
    return " ".join(parts)


def _market_probability_over(
    artifact: dict, predicted_total: float, market_total: float | None
) -> float | None:
    if market_total is None or pd.isna(market_total):
        return None
    residuals = np.asarray(artifact["residuals"], dtype=float)
    return float(np.mean(residuals > (float(market_total) - predicted_total)))


def _lean(probability: float | None) -> str:
    if probability is None:
        return "—"
    if probability >= LEAN_OVER:
        return "OVER"
    if probability <= LEAN_UNDER:
        return "UNDER"
    return "—"


def _team_payload(state: pd.DataFrame) -> list[dict[str, str]]:
    return [
        {
            "abbreviation": str(row.team_abbreviation),
            "name": str(row.team_name),
        }
        for row in state.sort_values("team_abbreviation").itertuples(index=False)
    ]


# ---------------------------------------------------------------------------
# Lesiones y disponibilidad (ESPN)
# ---------------------------------------------------------------------------


def _name_key(value: Any) -> str:
    """Normaliza nombres para cruzar ESPN con las estadísticas NBA."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _search_key(value: Any) -> str:
    return re.sub(r"\s+", " ", _name_key(value))


def _injury_number(value: Any, digits: int = 1) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        number = float(value)
        return round(number, digits) if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _injury_status_key(status: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(status or "").strip().casefold()).strip("-")


def _injury_status_label(status: Any) -> str:
    key = _injury_status_key(status)
    label = INJURY_STATUS_LABELS.get(key.replace("-", " "))
    return label or INJURY_STATUS_LABELS.get(key) or str(status or "Sin estado")


def _injury_status_class(status: Any) -> str:
    key = _injury_status_key(status)
    if key == "out":
        return "out"
    if key == "day-to-day":
        return "day"
    if key in {"doubtful", "questionable"}:
        return "doubtful"
    if key == "probable":
        return "probable"
    return "other"


def _injury_status_priority(status: Any) -> int:
    return {
        "out": 0,
        "doubtful": 1,
        "questionable": 2,
        "day": 3,
        "probable": 4,
    }.get(_injury_status_class(status), 5)


def _fetch_espn_injuries_raw() -> tuple[dict[str, Any] | None, bool, str | None]:
    """Descarga el parte global de ESPN con caché y tolerancia a fallos."""
    now = time.time()
    cached = INJURY_CACHE.get("raw")
    cached_at = float(INJURY_CACHE.get("at") or 0)
    if cached is not None and now - cached_at < INJURY_CACHE_SECONDS:
        return cached, False, None

    try:
        response = httpx.get(
            ESPN_INJURIES_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; nba-ml/0.6)",
                "Accept": "application/json",
            },
            timeout=LIVE_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("injuries"), list):
            raise ValueError("ESPN no devolvió un parte de lesiones válido")
    except Exception as error:
        if cached is not None:
            return cached, True, str(error)
        return None, False, str(error)

    INJURY_CACHE["raw"] = data
    INJURY_CACHE["at"] = now
    return data, False, None


def _injury_player_lookup() -> tuple[
    dict[tuple[str, str], dict[str, Any]], dict[str, list[dict[str, Any]]]
]:
    """Cruza los nombres de ESPN con las medias por partido de 2026-27."""
    try:
        players = _players("2026-27", "pergame")
    except HTTPException:
        return {}, {}
    if players is None or players.empty:
        return {}, {}

    frame = players.copy()
    for column in ["gp", "min", "pts", "reb", "ast", "pra"]:
        if column not in frame.columns:
            frame[column] = np.nan
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for raw_team, group in frame.groupby("team", dropna=False):
        team = _nba_abbreviation(raw_team)
        group = group[pd.to_numeric(group["gp"], errors="coerce").fillna(0) > 0].copy()
        if group.empty:
            continue
        group = group.sort_values(
            ["pra", "pts", "player_name"], ascending=[False, False, True], na_position="last"
        )
        points_order = group.sort_values(
            ["pts", "pra", "player_name"], ascending=[False, False, True], na_position="last"
        ).reset_index(drop=True)
        points_rank = {
            int(row["player_id"]): index + 1
            for index, row in points_order.iterrows()
            if not pd.isna(row.get("player_id"))
        }
        team_players = len(group)
        for index, row in enumerate(group.to_dict("records"), start=1):
            player_id = row.get("player_id")
            player_name = str(row.get("player_name") or "").strip()
            if not player_name or pd.isna(player_id):
                continue
            games_played = pd.to_numeric(row.get("gp"), errors="coerce")
            item = {
                "player_id": int(player_id),
                "player_name": player_name,
                "gp": 0 if pd.isna(games_played) else int(games_played),
                "min": _injury_number(row.get("min")),
                "pts": _injury_number(row.get("pts")),
                "reb": _injury_number(row.get("reb")),
                "ast": _injury_number(row.get("ast")),
                "pra": _injury_number(row.get("pra")),
                "pra_rank": index,
                "pts_rank": points_rank.get(int(player_id)),
                "team_players": team_players,
            }
            lookup[(team, _name_key(player_name))] = item
            by_name.setdefault(_name_key(player_name), []).append(item)
    return lookup, by_name


def _injury_counts(injuries: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "injury_count": len(injuries),
        "out_count": 0,
        "day_to_day_count": 0,
        "questionable_count": 0,
        "doubtful_count": 0,
        "probable_count": 0,
    }
    for injury in injuries:
        status_class = injury.get("status_class")
        if status_class == "out":
            counts["out_count"] += 1
        elif status_class == "day":
            counts["day_to_day_count"] += 1
        elif status_class == "doubtful":
            if _injury_status_key(injury.get("status")) == "questionable":
                counts["questionable_count"] += 1
            else:
                counts["doubtful_count"] += 1
        elif status_class == "probable":
            counts["probable_count"] += 1
    return counts


def _injury_impact_summary(injuries: list[dict[str, Any]]) -> str:
    if not injuries:
        return "Sin bajas reportadas por ESPN."
    counts = _injury_counts(injuries)
    parts: list[str] = []
    if counts["out_count"]:
        parts.append(f"{counts['out_count']} baja{'s' if counts['out_count'] != 1 else ''}")
    if counts["day_to_day_count"]:
        parts.append(f"{counts['day_to_day_count']} día a día")
    if counts["questionable_count"]:
        parts.append(f"{counts['questionable_count']} en duda")
    if counts["doubtful_count"]:
        parts.append(
            f"{counts['doubtful_count']} duda{'s' if counts['doubtful_count'] != 1 else ''} alta"
        )
    principal = injuries[0]
    stats = principal.get("stats") or {}
    detail = principal.get("player_name") or "jugador sin nombre"
    if stats.get("pra") is not None:
        detail += f" ({stats['pra']:.1f} PRA)"
    parts.append(f"Principal: {detail}")
    return " · ".join(parts)


def _injury_report(
    state: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Construye el parte por equipo y metadatos; nunca inventa lesiones."""
    raw, stale, _error = _fetch_espn_injuries_raw()
    lookup, by_name = _injury_player_lookup()

    teams: dict[str, dict[str, Any]] = {}
    team_names = {_name_key(row.team_name): str(row.team_abbreviation) for row in state.itertuples(index=False)}
    for row in state.itertuples(index=False):
        abbreviation = str(row.team_abbreviation)
        teams[abbreviation] = {
            "team": abbreviation,
            "team_name": str(row.team_name),
            "team_logo_url": _team_logo_url(getattr(row, "team_id", None)),
            "injuries": [],
        }

    espn_teams = raw.get("injuries", []) if raw else []
    for espn_team in espn_teams:
        if not isinstance(espn_team, dict):
            continue
        team_items = espn_team.get("injuries") or []
        if not isinstance(team_items, list):
            continue
        for item in team_items:
            if not isinstance(item, dict):
                continue
            athlete = item.get("athlete") or {}
            athlete_team = athlete.get("team") or {}
            abbreviation = _nba_abbreviation(athlete_team.get("abbreviation"))
            if not abbreviation:
                abbreviation = team_names.get(
                    _name_key(espn_team.get("displayName") or athlete_team.get("displayName")),
                    "",
                )
            if not abbreviation:
                continue
            name = str(athlete.get("displayName") or "").strip()
            if not name:
                continue
            if abbreviation not in teams:
                logos = athlete_team.get("logos") or []
                logo = next(
                    (
                        logo.get("href")
                        for logo in logos
                        if isinstance(logo, dict) and logo.get("href")
                    ),
                    None,
                )
                teams[abbreviation] = {
                    "team": abbreviation,
                    "team_name": str(
                        athlete_team.get("displayName")
                        or espn_team.get("displayName")
                        or abbreviation
                    ),
                    "team_logo_url": logo,
                    "injuries": [],
                }

            match = lookup.get((abbreviation, _name_key(name)))
            if match is None:
                candidates = by_name.get(_name_key(name), [])
                if len(candidates) == 1:
                    match = candidates[0]
            stats = (
                {
                    key: match.get(key)
                    for key in [
                        "gp",
                        "min",
                        "pts",
                        "reb",
                        "ast",
                        "pra",
                        "pra_rank",
                        "pts_rank",
                        "team_players",
                    ]
                }
                if match
                else {
                    "gp": None,
                    "min": None,
                    "pts": None,
                    "reb": None,
                    "ast": None,
                    "pra": None,
                    "pra_rank": None,
                    "pts_rank": None,
                    "team_players": None,
                }
            )
            gp = stats.get("gp")
            pra_rank = stats.get("pra_rank")
            is_key_player = bool(gp and gp >= 2 and pra_rank is not None and pra_rank <= 5)
            if not match:
                role_note = "Sin estadísticas 2026-27"
            elif not gp:
                role_note = "Sin minutos registrados"
            elif gp < 2:
                role_note = f"Muestra corta ({gp} PJ)"
            elif is_key_player:
                role_note = f"Top {pra_rank} del equipo en PRA"
            else:
                role_note = "Rotación"

            status = str(item.get("status") or "Sin estado").strip()
            updated = item.get("date")
            date_value = None
            try:
                parsed = pd.to_datetime(updated, utc=True, errors="coerce")
                if pd.notna(parsed):
                    date_value = parsed.tz_convert(NBA_ET).strftime("%Y-%m-%d")
            except Exception:
                date_value = None
            position = athlete.get("position") or {}
            headshot = athlete.get("headshot") or {}
            injury = {
                "player_name": name,
                "player_id": match.get("player_id") if match else None,
                "position": str(position.get("abbreviation") or position.get("displayName") or ""),
                "status": status,
                "status_label": _injury_status_label(status),
                "status_class": _injury_status_class(status),
                "date": date_value,
                "updated_at": updated if isinstance(updated, str) else None,
                "short_comment": str(
                    item.get("shortComment") or item.get("longComment") or ""
                ).strip(),
                "headshot_url": headshot.get("href") if isinstance(headshot, dict) else None,
                "stats": stats,
                "is_key_player": is_key_player,
                "role_note": role_note,
            }
            teams[abbreviation]["injuries"].append(injury)

    report_teams: list[dict[str, Any]] = []
    for team in teams.values():
        injuries = team["injuries"]
        injuries.sort(
            key=lambda injury: (
                _injury_status_priority(injury.get("status")),
                -(injury.get("stats", {}).get("pra") or -1),
                injury.get("player_name") or "",
            )
        )
        team.update(_injury_counts(injuries))
        team["key_absentees"] = [
            {
                "player_name": injury["player_name"],
                "status": injury["status"],
                "status_label": injury["status_label"],
                "pra": injury.get("stats", {}).get("pra"),
                "is_key_player": injury.get("is_key_player"),
            }
            for injury in injuries
            if injury.get("status_class") == "out"
        ][:3]
        team["impact_summary"] = _injury_impact_summary(injuries)
        team["model_adjusted"] = False
        report_teams.append(team)
    report_teams.sort(key=lambda item: (-item["injury_count"], item["team_name"]))

    source_updated_at = None
    season = None
    if raw:
        season = (raw.get("season") or {}).get("displayName")
        try:
            timestamp = pd.to_datetime(raw.get("timestamp"), utc=True, errors="coerce")
            if pd.notna(timestamp):
                source_updated_at = timestamp.tz_convert(NBA_ET).strftime("%Y-%m-%d %H:%M ET")
        except Exception:
            source_updated_at = None

    warning = None
    if raw is None:
        warning = (
            "No se pudo consultar el parte de lesiones de ESPN. "
            "La app sigue funcionando, pero no hay datos de disponibilidad."
        )
    elif stale:
        warning = (
            "No se pudo actualizar el parte de lesiones de ESPN; se muestra la "
            f"última copia disponible ({source_updated_at or 'sin fecha'})."
        )
    meta = {
        "generated_at": pd.Timestamp.now(tz=NBA_ET).strftime("%Y-%m-%d %H:%M ET"),
        "source": "ESPN",
        "source_url": ESPN_INJURIES_PAGE,
        "season": season,
        "source_updated_at": source_updated_at,
        "stale": stale,
        "warning": warning,
        "model_adjusted": False,
        "model_note": INJURY_MODEL_NOTE,
        "impact_note": INJURY_IMPACT_NOTE,
        "limitations": INJURY_LIMITATIONS,
    }
    return report_teams, meta


def _injuries_response(
    team: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    state = _state()
    teams, meta = _injury_report(state)
    selected = {
        item.strip().upper()
        for item in (team or "").split(",")
        if item.strip()
    }
    status_key = _injury_status_key(status)
    query = _search_key(q)

    filtered_teams: list[dict[str, Any]] = []
    for team_row in teams:
        if selected and team_row["team"].upper() not in selected:
            continue
        injuries = list(team_row["injuries"])
        if status_key:
            injuries = [
                injury
                for injury in injuries
                if status_key
                in {
                    _injury_status_key(injury.get("status")),
                    _injury_status_key(injury.get("status_label")),
                }
            ]
        if query:
            injuries = [
                injury
                for injury in injuries
                if query
                in _search_key(
                    " ".join(
                        [
                            str(team_row.get("team") or ""),
                            str(team_row.get("team_name") or ""),
                            str(injury.get("player_name") or ""),
                            str(injury.get("status") or ""),
                            str(injury.get("status_label") or ""),
                            str(injury.get("short_comment") or ""),
                        ]
                    )
                )
            ]

        team_matches_query = bool(
            query and query in _search_key(f"{team_row.get('team', '')} {team_row.get('team_name', '')}")
        )
        if (status_key or query) and not injuries and not team_matches_query:
            continue
        summary = dict(team_row)
        summary.update(_injury_counts(injuries))
        summary["injuries"] = injuries
        summary["key_absentees"] = [
            {
                "player_name": injury["player_name"],
                "status": injury["status"],
                "status_label": injury["status_label"],
                "pra": injury.get("stats", {}).get("pra"),
                "is_key_player": injury.get("is_key_player"),
            }
            for injury in injuries
            if injury.get("status_class") == "out"
        ][:3]
        summary["impact_summary"] = _injury_impact_summary(injuries)
        filtered_teams.append(summary)

    flat = [injury for item in filtered_teams for injury in item["injuries"]]
    status_counts: dict[str, int] = {}
    for injury in flat:
        raw_status = injury.get("status") or "Sin estado"
        status_counts[raw_status] = status_counts.get(raw_status, 0) + 1
    return {
        **meta,
        "count": len(flat),
        "team_count": sum(1 for item in filtered_teams if item["injury_count"]),
        "status_counts": status_counts,
        "teams": filtered_teams,
    }


def _injury_team_summary(team: dict[str, Any] | None) -> dict[str, Any]:
    if not team:
        return {
            "team": None,
            "team_name": None,
            "count": 0,
            "out_count": 0,
            "day_to_day_count": 0,
            "players": [],
            "impact_summary": "Sin datos de ESPN para este equipo.",
        }
    return {
        "team": team.get("team"),
        "team_name": team.get("team_name"),
        "count": team.get("injury_count", 0),
        "out_count": team.get("out_count", 0),
        "day_to_day_count": team.get("day_to_day_count", 0),
        "players": [
            {
                "player_name": injury.get("player_name"),
                "status": injury.get("status"),
                "status_label": injury.get("status_label"),
                "status_class": injury.get("status_class"),
                "is_key_player": injury.get("is_key_player"),
                "pra": injury.get("stats", {}).get("pra"),
            }
            for injury in team.get("injuries", [])
        ],
        "impact_summary": team.get("impact_summary"),
    }


def _bb_injury_context(
    event: dict[str, Any],
    index: dict[str, dict[str, Any]],
    meta: dict[str, Any],
) -> dict[str, Any]:
    return {
        "home": _injury_team_summary(index.get(str(event.get("home") or ""))),
        "away": _injury_team_summary(index.get(str(event.get("away") or ""))),
        "model_adjusted": False,
        "source": "ESPN",
        "source_updated_at": meta.get("source_updated_at"),
        "source_url": meta.get("source_url"),
        "warning": meta.get("warning"),
        "note": INJURY_MODEL_NOTE,
    }



def _prediction_warning(result: dict[str, Any], as_of: pd.Timestamp) -> str | None:
    oldest = min(
        pd.Timestamp(result["home_last_game"]),
        pd.Timestamp(result["away_last_game"]),
    )
    gap = (as_of - oldest).days
    if gap > 14:
        return (
            f"El último partido de uno de los equipos fue hace {gap} días. "
            "El estado de los equipos está desactualizado; la predicción "
            "asume descanso maximo."
        )
    return None


# ---------------------------------------------------------------------------
# Bet Builder: mercados y probabilidades
# ---------------------------------------------------------------------------

BET_BUILDER_LIMITATIONS = [
    (
        "Las probabilidades se derivan de los residuos históricos del modelo "
        "Ridge existente. Son estimaciones del modelo, no probabilidades "
        "calibradas como cuotas de una casa de apuestas."
    ),
    (
        "La probabilidad conjunta solo se calcula cuando todas las selecciones "
        "pertenecen a partidos distintos. En ese caso se aplica el supuesto "
        "explícito de independencia; no se modela correlación por lesiones, "
        "noticias, alineaciones o contexto de partido."
    ),
    (
        "Las selecciones del mismo partido se muestran con su probabilidad "
        "individual, pero la probabilidad conjunta queda no disponible porque "
        "no existe un modelo de dependencia validado."
    ),
    (
        "Las líneas personalizadas las introduce el usuario. No son líneas de "
        "mercado y se identifican como tales en el ticket y en el análisis. "
        "Con líneas enteras, el empate (push) no se modela; se recomienda usar "
        "líneas terminadas en .5."
    ),
    (
        "Los mercados de jugador están deshabilitados: el modelo de props tiene "
        "proyecciones y confianza, pero no líneas de mercado ni una "
        "distribución calibrada por línea."
    ),
    (
        "El resumen de lesiones de ESPN es informativo: se muestra junto al "
        "partido, pero no ajusta las probabilidades ni el ticket."
    ),
]

BET_BUILDER_PLAYER_MARKETS = {
    "available": False,
    "label": "Mercados de jugador",
    "stats": ["Puntos", "Rebotes", "Asistencias", "PRA", "Triples"],
    "reason": (
        "El modelo de props entrega proyecciones por jugador, pero el proyecto "
        "no dispone de líneas de mercado de props ni de probabilidades "
        "calibradas para una línea concreta. Para no inventar líneas ni "
        "probabilidades, estos mercados no se pueden añadir al ticket."
    ),
    "next_step": (
        "Integrar una fuente de líneas de props y calibrar la probabilidad de "
        "cada mercado contra su línea."
    ),
}


def _bb_market_number(
    market: dict[str, Any] | pd.Series | None, key: str
) -> float | None:
    if market is None:
        return None
    try:
        value = market.get(key)
    except AttributeError:
        return None
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bb_source(provider: Any) -> str:
    if provider is None or pd.isna(provider):
        return "ESPN"
    return f"ESPN / {provider}"


def _bb_option(
    label: str,
    side: str,
    probability: float | None = None,
    american_odds: Any = None,
) -> dict[str, Any]:
    decimal = None
    if american_odds is not None and not pd.isna(american_odds):
        try:
            decimal = american_to_decimal(float(american_odds))
        except ValueError:
            decimal = None
    return {
        "side": side,
        "label": label,
        "probability": _rounded(probability, 6),
        "american_odds": None if american_odds is None else american_odds,
        "decimal_odds": _rounded(decimal, 6),
    }


def _bb_total_market(
    artifact: dict,
    result: dict[str, Any],
    market: dict[str, Any] | pd.Series | None,
    provider: Any,
) -> dict[str, Any]:
    projected = float(result["predicted_total"])
    market_line = _bb_market_number(market, "market_total")
    if market_line is not None:
        over = total_side_probability(artifact, projected, market_line, "OVER")
        under = total_side_probability(artifact, projected, market_line, "UNDER")
        over_odds = _bb_market_number(market, "over_odds")
        under_odds = _bb_market_number(market, "under_odds")
        options = [
            _bb_option("Over", "OVER", over, over_odds),
            _bb_option("Under", "UNDER", under, under_odds),
        ]
        available = over is not None and under is not None
        return {
            "id": "total",
            "type": "game_total",
            "label": "Total del partido",
            "line": _rounded(market_line, 2),
            "line_source": "market",
            "requires_line": False,
            "available": available,
            "projected": _rounded(projected, 1),
            "method": "empirical_residuals",
            "provider": None if provider is None else str(provider),
            "source": _bb_source(provider),
            "note": (
                f"Línea de mercado {market_line:.1f}. Las cuotas son "
                "informativas y pueden cambiar."
            ),
            "reason": None if available else "No hay residuos suficientes para calcular la probabilidad.",
            "options": options,
        }

    return {
        "id": "total",
        "type": "game_total",
        "label": "Total del partido",
        "line": None,
        "line_source": "custom",
        "requires_line": True,
        "available": True,
        "projected": _rounded(projected, 1),
        "method": "empirical_residuals",
        "provider": None,
        "source": "Modelo Ridge + línea personalizada",
        "note": (
            "No hay línea de mercado para este partido. Introduce una línea "
            "personalizada; la probabilidad se calcula con los residuos del "
            "modelo y no se presenta como cuota de mercado."
        ),
        "reason": None,
        "options": [
            _bb_option("Over", "OVER"),
            _bb_option("Under", "UNDER"),
        ],
    }


def _bb_winner_market(result: dict[str, Any], home_name: str, away_name: str) -> dict[str, Any]:
    home_probability = result.get("home_win_probability")
    if home_probability is None or pd.isna(home_probability):
        home_probability = None
    home = winner_side_probability(home_probability, "HOME")
    away = winner_side_probability(home_probability, "AWAY")
    available = home is not None and away is not None
    return {
        "id": "winner",
        "type": "winner",
        "label": "Ganador del partido",
        "line": None,
        "line_source": "model",
        "requires_line": False,
        "available": available,
        "projected": None,
        "method": "margin_residuals",
        "provider": None,
        "source": "Modelo Ridge / residuos de margen",
        "note": "Probabilidad estimada por el modelo; sin cuota de mercado.",
        "reason": None if available else "El modelo no dispone de probabilidad de ganador.",
        "options": [
            _bb_option(home_name, "HOME", home),
            _bb_option(away_name, "AWAY", away),
        ],
    }


def _bb_handicap_market(
    result: dict[str, Any],
    market: dict[str, Any] | pd.Series | None,
    handicap_info: dict[str, Any],
    home_name: str,
    away_name: str,
    provider: Any,
) -> dict[str, Any]:
    spread = _bb_market_number(market, "market_spread")
    home_cover = result.get("home_cover_probability")
    if home_cover is None or pd.isna(home_cover):
        home_cover = None
    home = handicap_side_probability(home_cover, "HOME")
    away = handicap_side_probability(home_cover, "AWAY")
    available = spread is not None and home is not None and away is not None
    label = handicap_info.get("market_handicap")
    if not label and spread is not None:
        if spread < 0:
            label = f"{home_name} -{abs(spread):.1f}"
        elif spread > 0:
            label = f"{away_name} -{abs(spread):.1f}"
        else:
            label = "Pick'em"
    return {
        "id": "handicap",
        "type": "handicap",
        "label": "Hándicap",
        "line": _rounded(spread, 2) if spread is not None else None,
        "line_source": "market",
        "requires_line": False,
        "available": available,
        "projected": _rounded(result.get("model_home_spread"), 1),
        "method": "margin_residuals",
        "provider": None if provider is None else str(provider),
        "source": _bb_source(provider) if spread is not None else "Modelo Ridge",
        "note": (
            f"Hándicap de mercado: {label}. No se incluye cuota de hándicap."
            if label
            else "Sin línea de hándicap de mercado."
        ),
        "reason": (
            None
            if available
            else "Sin línea de hándicap de mercado; el modelo no inventa una línea."
        ),
        "options": [
            _bb_option(f"{home_name} cubre", "HOME", home),
            _bb_option(f"{away_name} cubre", "AWAY", away),
        ],
    }


def _bb_team_total_market(
    market_id: str,
    label: str,
    venue: str,
    team_name: str,
    projected: Any,
) -> dict[str, Any]:
    projection = None
    if projected is not None and not pd.isna(projected):
        projection = float(projected)
    return {
        "id": market_id,
        "type": "team_total",
        "label": label,
        "line": None,
        "line_source": "custom",
        "requires_line": True,
        "available": projection is not None,
        "projected": _rounded(projection, 1),
        "method": "paired_residuals",
        "provider": None,
        "source": "Modelo Ridge / residuos emparejados + línea personalizada",
        "note": (
            f"Introduce una línea para {team_name}. No es una línea de mercado; "
            "la probabilidad se calcula con los residuos históricos del modelo."
        ),
        "reason": (
            None
            if projection is not None
            else "El modelo no dispone de proyección para este equipo."
        ),
        "venue": venue,
        "team": team_name,
        "options": [
            _bb_option("Over", "OVER"),
            _bb_option("Under", "UNDER"),
        ],
    }


def _bb_event_payload(
    game: Any,
    result: dict[str, Any],
    market: dict[str, Any] | pd.Series | None,
    names: dict[str, str],
    state_date: pd.Timestamp | None,
    odds_as_of: str | None,
    artifact: dict,
) -> dict[str, Any]:
    home_name = names.get(str(game.home), str(game.home))
    away_name = names.get(str(game.away), str(game.away))
    provider = market.get("market_provider") if market is not None else None
    handicap_info = _handicap_fields(result, home_name, away_name, market)
    markets = [
        _bb_total_market(artifact, result, market, provider),
        _bb_winner_market(result, home_name, away_name),
        _bb_handicap_market(
            result, market, handicap_info, home_name, away_name, provider
        ),
        _bb_team_total_market(
            "team_total_home",
            f"Total local · {home_name}",
            "home",
            home_name,
            result.get("projected_home_score"),
        ),
        _bb_team_total_market(
            "team_total_away",
            f"Total visitante · {away_name}",
            "away",
            away_name,
            result.get("projected_away_score"),
        ),
    ]
    low, high = result["interval_80"]
    event_date = pd.Timestamp(game.game_date)
    return {
        "event_id": str(game.game_id),
        "event_label": f"{away_name} @ {home_name}",
        "date": event_date.strftime("%Y-%m-%d"),
        "time_et": None if pd.isna(game.game_time_et) else str(game.game_time_et),
        "status": "Programado",
        "away": str(game.away),
        "home": str(game.home),
        "away_name": away_name,
        "home_name": home_name,
        "arena": getattr(game, "arena", None),
        "predicted_total": _rounded(result["predicted_total"], 1),
        "interval_low": _rounded(low, 1),
        "interval_high": _rounded(high, 1),
        "projected_home_score": _rounded(result.get("projected_home_score"), 1),
        "projected_away_score": _rounded(result.get("projected_away_score"), 1),
        "predicted_margin": _rounded(result.get("predicted_margin"), 1),
        "home_win_probability": _rounded(result.get("home_win_probability"), 6),
        "home_rest": result.get("home_rest"),
        "away_rest": result.get("away_rest"),
        "data_state_date": None if state_date is None else state_date.strftime("%Y-%m-%d"),
        "odds_as_of": odds_as_of if any(
            item.get("decimal_odds") is not None
            for item in markets[0].get("options", [])
        ) else None,
        "data_warning": _prediction_warning(result, event_date),
        "market_source": _bb_source(provider),
        "selectable_markets": sum(
            1
            for item in markets
            if item.get("available") and not item.get("requires_line")
        ),
        "custom_markets": sum(
            1
            for item in markets
            if item.get("available") and item.get("requires_line")
        ),
        "markets": markets,
    }


def _bb_state_date(state: pd.DataFrame) -> pd.Timestamp | None:
    dates = pd.to_datetime(state["last_game_date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.max().normalize()


def _bb_events(
    start: pd.Timestamp,
    days: int,
    schedule: pd.DataFrame,
    artifact: dict,
    state: pd.DataFrame,
) -> tuple[list[dict[str, Any]], int, pd.Timestamp | None, str | None]:
    end = start + pd.Timedelta(days=days - 1)
    dates = pd.to_datetime(schedule["game_date"]).dt.normalize()
    mask = dates.between(start, end) & schedule["status"].eq(1)
    games = schedule.loc[mask].sort_values(["game_date", "game_time_et", "game_id"])
    market_frame = pd.DataFrame()
    if not games.empty:
        market_frame = _odds_for_dates(list(games["game_date"]), schedule)
        if not market_frame.empty:
            market_frame = market_frame.set_index("game_id")
    names = _team_names(state)
    state_date = _bb_state_date(state)
    odds_as_of = pd.Timestamp.now(tz=NBA_ET).strftime("%Y-%m-%d %H:%M ET")
    events: list[dict[str, Any]] = []
    skipped = 0
    for game in games.itertuples(index=False):
        market = None
        if not market_frame.empty and game.game_id in market_frame.index:
            market = market_frame.loc[game.game_id]
        market_total = _bb_market_number(market, "market_total")
        spread = _bb_market_number(market, "market_spread")
        reference_line = market_total if market_total is not None else 225.5
        try:
            result = predict_game(
                artifact,
                state,
                home=game.home,
                away=game.away,
                as_of=pd.Timestamp(game.game_date),
                line=reference_line,
                handicap=spread,
                warn_stale=False,
            )
        except ValueError:
            skipped += 1
            continue
        events.append(
            _bb_event_payload(
                game, result, market, names, state_date, odds_as_of, artifact
            )
        )
    return events, skipped, state_date, odds_as_of


def _bb_line_limits(market_id: str) -> tuple[float, float]:
    if market_id == "total":
        return 100.0, 350.0
    if market_id in {"team_total_home", "team_total_away"}:
        return 40.0, 220.0
    return -80.0, 80.0


def _bb_valid_line(
    line: float | None, market_id: str
) -> tuple[float | None, str | None]:
    if line is None:
        return None, "Falta la línea del mercado o una línea personalizada."
    try:
        value = float(line)
    except (TypeError, ValueError):
        return None, "La línea no es numérica."
    if not math.isfinite(value):
        return None, "La línea no es finita."
    low, high = _bb_line_limits(market_id)
    if value < low or value > high:
        return None, f"La línea debe estar entre {low:g} y {high:g}."
    return value, None


def _bb_selection_payload(
    event: dict[str, Any],
    selection: BetBuilderSelectionIn,
    artifact: dict,
    schedule_row: pd.Series | None,
) -> dict[str, Any]:
    market_id = str(selection.market).strip().lower()
    side = str(selection.side).strip().upper()
    selection_line = selection.line
    event_id = str(selection.event_id)
    base = {
        "event_id": event_id,
        "event_date": event.get("date"),
        "event_label": event.get("event_label"),
        "market": market_id,
        "market_label": None,
        "side": side,
        "side_label": None,
        "line": None,
        "line_source": None,
        "probability": None,
        "probability_source": None,
        "decimal_odds": None,
        "american_odds": None,
        "data_source": None,
        "data_as_of": event.get("data_state_date"),
        "odds_as_of": None,
        "status": "ok",
        "stale_reason": None,
        "warning": None,
        "is_custom_line": False,
        "selection_id": None,
    }

    if schedule_row is None:
        base["status"] = "stale"
        base["stale_reason"] = "El partido ya no existe en el calendario."
        return base
    if int(schedule_row.get("status", 0)) != 1:
        base["status"] = "stale"
        base["stale_reason"] = "El partido ya no está próximo o está finalizado."
        return base

    market = next(
        (item for item in event.get("markets", []) if item.get("id") == market_id),
        None,
    )
    if market is None:
        base["status"] = "invalid"
        base["stale_reason"] = "Mercado desconocido."
        return base
    if not market.get("available"):
        base["status"] = "invalid"
        base["market_label"] = market.get("label")
        base["stale_reason"] = market.get("reason") or "Mercado no disponible."
        return base
    base["market_label"] = market.get("label")
    base["data_source"] = market.get("source")
    market_line = market.get("line")
    line = selection_line
    line_error = None

    if market_id == "total":
        if market_line is not None:
            if line is None:
                line = float(market_line)
            else:
                line, line_error = _bb_valid_line(line, market_id)
                if line is None:
                    base["status"] = "invalid"
                    base["stale_reason"] = line_error
                    return base
                if abs(line - float(market_line)) > 1e-9:
                    base["status"] = "stale"
                    base["stale_reason"] = (
                        f"La línea de mercado cambió de {line:.1f} a "
                        f"{float(market_line):.1f}. Elimina y vuelve a añadir la selección."
                    )
                    base["line"] = _rounded(line, 2)
                    base["line_source"] = "market"
                    return base
            base["line_source"] = "market"
            option = next(
                (item for item in market.get("options", []) if item.get("side") == side),
                None,
            )
            if option is None:
                base["status"] = "invalid"
                base["stale_reason"] = "El lado debe ser OVER o UNDER."
                return base
            base["probability"] = option.get("probability")
            base["probability_source"] = "empirical_residuals"
            base["decimal_odds"] = option.get("decimal_odds")
            base["american_odds"] = option.get("american_odds")
            base["odds_as_of"] = event.get("odds_as_of")
            base["side_label"] = option.get("label")
        else:
            if side not in {"OVER", "UNDER"}:
                base["status"] = "invalid"
                base["stale_reason"] = "El lado debe ser OVER o UNDER."
                return base
            line, line_error = _bb_valid_line(line, market_id)
            if line is None:
                base["status"] = "invalid"
                base["stale_reason"] = line_error
                return base
            base["is_custom_line"] = True
            base["line_source"] = "custom"
            tail = _rounded(line, 2)
            probability = total_side_probability(
                artifact, float(event["predicted_total"]), float(line), side
            )
            base["probability"] = _rounded(probability, 6)
            base["probability_source"] = "empirical_residuals"
            base["data_source"] = "Modelo Ridge + línea personalizada"
            base["side_label"] = "Over" if side == "OVER" else "Under"
            base["note"] = f"Línea personalizada {tail:.1f}; no es una línea de mercado."
    elif market_id in {"team_total_home", "team_total_away"}:
        if side not in {"OVER", "UNDER"}:
            base["status"] = "invalid"
            base["stale_reason"] = "El lado debe ser OVER o UNDER."
            return base
        line, line_error = _bb_valid_line(line, market_id)
        if line is None:
            base["status"] = "invalid"
            base["stale_reason"] = line_error
            return base
        base["is_custom_line"] = True
        base["line_source"] = "custom"
        venue = "home" if market_id == "team_total_home" else "away"
        probability = team_total_side_probability(
            artifact,
            float(event["projected_home_score"]),
            float(event["projected_away_score"]),
            float(line),
            side,
            venue,
        )
        base["probability"] = _rounded(probability, 6)
        base["probability_source"] = "paired_residuals"
        base["data_source"] = "Modelo Ridge / residuos emparejados + línea personalizada"
        base["side_label"] = "Over" if side == "OVER" else "Under"
        base["note"] = f"Línea personalizada {line:.1f}; no es una línea de mercado."
    elif market_id == "winner":
        option = next(
            (item for item in market.get("options", []) if item.get("side") == side),
            None,
        )
        if option is None:
            base["status"] = "invalid"
            base["stale_reason"] = "El lado debe ser HOME o AWAY."
            return base
        if line is not None:
            base["status"] = "invalid"
            base["stale_reason"] = "El mercado de ganador no usa línea."
            return base
        base["side_label"] = option.get("label")
        base["probability"] = option.get("probability")
        base["probability_source"] = "margin_residuals"
        base["line_source"] = "model"
    elif market_id == "handicap":
        if market_line is None:
            base["status"] = "invalid"
            base["stale_reason"] = "No hay línea de hándicap de mercado."
            return base
        if line is None:
            line = float(market_line)
        else:
            line, line_error = _bb_valid_line(line, market_id)
            if line is None:
                base["status"] = "invalid"
                base["stale_reason"] = line_error
                return base
            if abs(line - float(market_line)) > 1e-9:
                base["status"] = "stale"
                base["stale_reason"] = (
                    f"El hándicap cambió de {line:.1f} a {float(market_line):.1f}. "
                    "Elimina y vuelve a añadir la selección."
                )
                base["line"] = _rounded(line, 2)
                base["line_source"] = "market"
                return base
        option = next(
            (item for item in market.get("options", []) if item.get("side") == side),
            None,
        )
        if option is None:
            base["status"] = "invalid"
            base["stale_reason"] = "El lado debe ser HOME o AWAY."
            return base
        base["line"] = _rounded(line, 2)
        base["line_source"] = "market"
        base["side_label"] = option.get("label")
        base["probability"] = option.get("probability")
        base["probability_source"] = "margin_residuals"
        base["data_source"] = market.get("source")
    else:
        base["status"] = "invalid"
        base["stale_reason"] = "Mercado no soportado."
        return base

    if base["line"] is None and line is not None:
        base["line"] = _rounded(line, 2)
    line_key = "" if base["line"] is None else f"{float(base['line']):.2f}"
    base["selection_id"] = f"{event_id}:{market_id}:{side}:{line_key}"
    if base["probability"] is None:
        base["status"] = "invalid"
        base["stale_reason"] = "No se pudo calcular una probabilidad válida para esta línea."
        return base
    try:
        base["probability"] = validate_probability(base["probability"])
    except ValueError as error:
        base["status"] = "invalid"
        base["stale_reason"] = str(error)
        return base
    if base["probability"] is None:
        base["status"] = "invalid"
        base["stale_reason"] = "Probabilidad no disponible."
        return base
    return base


def _bb_same_game_groups(selections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for selection in selections:
        groups.setdefault(str(selection["event_id"]), []).append(selection)
    return [
        {
            "event_id": event_id,
            "event_label": items[0].get("event_label"),
            "count": len(items),
            "markets": [
                {
                    "market": item.get("market_label"),
                    "side": item.get("side_label"),
                    "line": item.get("line"),
                }
                for item in items
            ],
        }
        for event_id, items in groups.items()
        if len(items) > 1
    ]


def _bb_mark_problems(selections: list[dict[str, Any]]) -> None:
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for selection in selections:
        key = (
            selection.get("event_id"),
            selection.get("market"),
            selection.get("line"),
            selection.get("side"),
        )
        if key in seen:
            selection["status"] = "invalid"
            selection["stale_reason"] = "Selección duplicada."
            seen[key]["status"] = "invalid"
            seen[key]["stale_reason"] = "Selección duplicada."
        else:
            seen[key] = selection

    by_market: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for selection in selections:
        key = (
            selection.get("event_id"),
            selection.get("market"),
            selection.get("line"),
        )
        by_market.setdefault(key, []).append(selection)
    for items in by_market.values():
        sides = {item.get("side") for item in items}
        if len(sides) > 1:
            for item in items:
                item["status"] = "invalid"
                item["stale_reason"] = (
                    "Selecciones incompatibles: mismo mercado y línea con lados opuestos."
                )


def _bb_odds_product(selections: list[dict[str, Any]]) -> float | None:
    if not selections:
        return None
    product = 1.0
    for selection in selections:
        odds = selection.get("decimal_odds")
        if odds is None:
            return None
        try:
            product *= validate_decimal_odds(odds)
        except ValueError:
            return None
    return product


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> JSONResponse:
    return _ok({"status": "ok", "service": "nba-ml", "version": app.version})


@app.get("/api/meta")
def meta(today: str | None = None) -> JSONResponse:
    artifact = _artifact()
    state = _state()
    day = _today(today)
    last_state = pd.to_datetime(state["last_game_date"]).max().normalize()
    return _ok(
        {
            "model_name": artifact["model_name"],
            "trained_through": artifact["trained_through"],
            "n_features": len(artifact["features"]),
            "residual_sigma": artifact["residual_sigma"],
            "today": day.strftime("%Y-%m-%d"),
            "current_season": _season_for(day),
            "min_date": (last_state + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            "last_state_date": last_state.strftime("%Y-%m-%d"),
            "default_line": 225.5,
            "seasons": list(AVAILABLE_SEASONS),
            "players_seasons": ["2025-26", "2026-27"],
            "teams": _team_payload(state),
            "handicap_test_metrics": artifact.get("margin_test_metrics"),
            "caveat": (
                "Las proyecciones no incorporan lesiones ni alineaciones. El "
                "parte de ESPN se muestra como contexto y no cambia el total, "
                "el hándicap ni las probabilidades. La línea de total es una "
                "referencia introducida por el usuario. Las cuotas de mercado, "
                "cuando aparecen, son informativas y no una recomendación de "
                "apuestas."
            ),
        }
    )


@app.get("/api/teams")
def teams() -> JSONResponse:
    return _ok({"teams": _team_payload(_state())})


@app.get("/api/injuries")
def injuries(
    team: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> JSONResponse:
    """Parte de lesiones de ESPN, sin ajustar las proyecciones del modelo."""
    return _ok(_injuries_response(team=team, status=status, q=q))


@app.get("/api/upcoming")
def upcoming(
    days: int = Query(7, ge=1, le=30),
    line: float = Query(225.5, ge=100, le=350),
    team: str | None = None,
    today: str | None = None,
    live: bool = False,
    odds: bool = False,
    analysis: bool = False,
) -> JSONResponse:
    artifact = _artifact()
    state = _state()
    schedule = _schedule()
    start = _today(today)
    end = start + pd.Timedelta(days=days - 1)
    live_updated = None
    if live:
        schedule, live_updated = _merge_live(schedule, _live_dates(today))

    dates = pd.to_datetime(schedule["game_date"]).dt.normalize()
    mask = dates.between(start, end) & schedule["status"].eq(1)
    selected = [item.strip().upper() for item in (team or "").split(",") if item.strip()]
    if selected:
        mask &= schedule["home"].isin(selected) | schedule["away"].isin(selected)

    games = schedule.loc[mask].sort_values(["game_date", "game_time_et", "game_id"])
    names = _team_names(state)
    market_frame = pd.DataFrame()
    if odds and not games.empty:
        market_frame = _odds_for_dates(list(games["game_date"]), schedule)
        if not market_frame.empty:
            market_frame = market_frame.set_index("game_id")

    rows: list[dict[str, Any]] = []
    skipped = 0
    for game in games.itertuples(index=False):
        market = None
        if not market_frame.empty and game.game_id in market_frame.index:
            market = market_frame.loc[game.game_id]

        handicap = None
        if market is not None:
            spread = market.get("market_spread")
            if spread is not None and not pd.isna(spread):
                handicap = float(spread)

        try:
            result = predict_game(
                artifact,
                state,
                home=game.home,
                away=game.away,
                as_of=pd.Timestamp(game.game_date),
                line=line,
                handicap=handicap,
                warn_stale=False,
            )
        except ValueError:
            skipped += 1
            continue

        low, high = result["interval_80"]
        probability = float(result["probability_over"])
        home_name = names.get(str(game.home), str(game.home))
        away_name = names.get(str(game.away), str(game.away))
        market_total = market.get("market_total") if market is not None else None
        if market_total is not None and pd.isna(market_total):
            market_total = None
        market_probability = _market_probability_over(
            artifact, float(result["predicted_total"]), market_total
        )
        market_side = _directional_side(market_probability)
        market_total_label = (
            _directional_label(market_probability, float(market_total))
            if market_total is not None
            else None
        )
        handicap_info = _handicap_fields(result, home_name, away_name, market)
        lean = _lean(probability)
        row = {
            "game_id": game.game_id,
            "date": pd.Timestamp(game.game_date).strftime("%Y-%m-%d"),
            "time_et": None if pd.isna(game.game_time_et) else str(game.game_time_et),
            "away": game.away,
            "home": game.home,
            "away_name": away_name,
            "home_name": home_name,
            "arena": getattr(game, "arena", None),
            "predicted_total": result["predicted_total"],
            "interval_low": low,
            "interval_high": high,
            "probability_over": probability,
            "lean": lean,
            "total_side": lean,
            "total_label": _total_label(lean, line),
            "home_rest": result["home_rest"],
            "away_rest": result["away_rest"],
            "predicted_margin": result.get("predicted_margin"),
            "model_home_spread": result.get("model_home_spread"),
            "projected_home_score": result.get("projected_home_score"),
            "projected_away_score": result.get("projected_away_score"),
            "home_win_probability": result.get("home_win_probability"),
            "market_total": market_total,
            "market_probability_over": market_probability,
            "market_total_side": market_side,
            "market_total_label": market_total_label,
            "market_spread": handicap_info["market_spread"],
            "market_handicap": handicap_info["market_handicap"],
            "market_details": market.get("market_details") if market is not None else None,
            "market_provider": market.get("market_provider") if market is not None else None,
            "over_odds": market.get("over_odds") if market is not None else None,
            "under_odds": market.get("under_odds") if market is not None else None,
            "possible_winner": handicap_info["possible_winner"],
            "model_handicap": handicap_info["model_handicap"],
            "model_handicap_line": handicap_info["model_handicap_line"],
            "model_handicap_favorite": handicap_info["model_handicap_favorite"],
            "handicap_edge": handicap_info["handicap_edge"],
            "handicap_pick": handicap_info["handicap_pick"],
            "home_cover_probability": handicap_info["home_cover_probability"],
            "away_cover_probability": handicap_info["away_cover_probability"],
            "analysis": (
                _analysis_text(
                    result,
                    home_name,
                    away_name,
                    line,
                    market,
                    market_probability,
                )
                if analysis
                else None
            ),
        }
        rows.append(row)

    last_state = pd.to_datetime(state["last_game_date"]).max().normalize()
    stale_days = max(0, (start - last_state).days)
    warning = None
    if stale_days > 7:
        warning = (
            f"El estado de los equipos es del {last_state.date()} "
            f"({stale_days} días). Las predicciones no incorporan partidos "
            "posteriores ni lesiones."
        )
    return _ok(
        {
            "season": _season_for(start),
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
            "line": line,
            "count": len(rows),
            "skipped": skipped,
            "warning": warning,
            "live_updated": live_updated,
            "market_source": (
                "ESPN / DraftKings"
                if odds and not market_frame.empty
                else None
            ),
            "handicap_note": (
                "El hándicap sugerido y el ganador probable son estimaciones del "
                "modelo. El hándicap y las cuotas de mercado, cuando aparecen, son "
                "informativos y pueden cambiar."
            ),
            "games": rows,
        }
    )


def _bb_event_lookup(
    event_ids: list[str],
    schedule: pd.DataFrame,
    artifact: dict,
    state: pd.DataFrame,
) -> tuple[dict[str, dict[str, Any]], pd.DataFrame]:
    index = schedule.drop_duplicates(subset=["game_id"], keep="last").set_index("game_id")
    events: dict[str, dict[str, Any]] = {}
    day_cache: dict[pd.Timestamp, dict[str, dict[str, Any]]] = {}
    for event_id in dict.fromkeys(event_ids):
        if event_id not in index.index:
            continue
        row = index.loc[event_id]
        try:
            status = int(row.get("status", 0))
        except (TypeError, ValueError):
            status = 0
        if status != 1:
            continue
        day = pd.Timestamp(row.get("game_date")).normalize()
        if day not in day_cache:
            day_events, _, _, _ = _bb_events(day, 1, schedule, artifact, state)
            day_cache[day] = {item["event_id"]: item for item in day_events}
        if event_id in day_cache[day]:
            events[event_id] = day_cache[day][event_id]
    return events, index


@app.get("/api/bet-builder/events")
def bet_builder_events(
    date: str | None = None,
    days: int = Query(7, ge=1, le=14),
    q: str | None = None,
    market: str | None = None,
    injuries: bool = False,
) -> JSONResponse:
    artifact = _artifact()
    state = _state()
    schedule = _schedule()
    start = _today(date)
    end = start + pd.Timedelta(days=days - 1)
    events, skipped, state_date, odds_as_of = _bb_events(
        start, days, schedule, artifact, state
    )
    injury_warning = None
    if injuries:
        injury_teams, injury_meta = _injury_report(state)
        injury_index = {item["team"]: item for item in injury_teams}
        injury_warning = injury_meta.get("warning")
        for event in events:
            event["injuries"] = _bb_injury_context(event, injury_index, injury_meta)
    query = (q or "").strip().casefold()
    if query:
        events = [
            event
            for event in events
            if query
            in " ".join(
                [
                    str(event.get("event_label") or ""),
                    str(event.get("home") or ""),
                    str(event.get("home_name") or ""),
                    str(event.get("away") or ""),
                    str(event.get("away_name") or ""),
                ]
            ).casefold()
        ]
    market_key = (market or "").strip().lower()
    if market_key in {"game_total", "total"}:
        for event in events:
            event["markets"] = [
                item for item in event["markets"] if item["type"] == "game_total"
            ]
        events = [event for event in events if event["markets"]]
    elif market_key == "winner":
        for event in events:
            event["markets"] = [
                item for item in event["markets"] if item["type"] == "winner"
            ]
        events = [event for event in events if event["markets"]]
    elif market_key == "handicap":
        for event in events:
            event["markets"] = [
                item for item in event["markets"] if item["type"] == "handicap"
            ]
        events = [event for event in events if event["markets"]]
    elif market_key in {"team_total", "team"}:
        for event in events:
            event["markets"] = [
                item for item in event["markets"] if item["type"] == "team_total"
            ]
        events = [event for event in events if event["markets"]]
    elif market_key == "players":
        events = []

    state_age_days = None
    state_warning = None
    if state_date is not None:
        state_age_days = max(0, (_today() - state_date).days)
        if state_age_days > 7:
            state_warning = (
                f"El estado de los equipos es del {state_date.date()} "
                f"({state_age_days} días). Las predicciones no incorporan "
                "partidos posteriores ni lesiones."
            )
    market_total_events = sum(
        1
        for event in events
        if any(
            item["id"] == "total" and item.get("line_source") == "market"
            for item in event.get("markets", [])
        )
    )
    has_odds = any(
        any(
            option.get("decimal_odds") is not None
            for option in item.get("options", [])
        )
        for event in events
        for item in event.get("markets", [])
    )
    return _ok(
        {
            "season": _season_for(start),
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
            "count": len(events),
            "skipped": skipped,
            "state_date": None if state_date is None else state_date.strftime("%Y-%m-%d"),
            "state_age_days": state_age_days,
            "warning": state_warning,
            "odds_source": "ESPN / DraftKings" if has_odds else None,
            "odds_as_of": odds_as_of if has_odds else None,
            "injuries_source": "ESPN" if injuries else None,
            "injuries_warning": injury_warning,
            "market_total_events": market_total_events,
            "limitations": BET_BUILDER_LIMITATIONS,
            "player_markets": BET_BUILDER_PLAYER_MARKETS,
            "events": events,
        }
    )


@app.post("/api/bet-builder/analyze")
def bet_builder_analyze(payload: BetBuilderAnalyzeIn) -> JSONResponse:
    artifact = _artifact()
    state = _state()
    schedule = _schedule()
    state_date = _bb_state_date(state)
    event_map, schedule_index = _bb_event_lookup(
        [selection.event_id for selection in payload.selections],
        schedule,
        artifact,
        state,
    )
    selections: list[dict[str, Any]] = []
    for selection in payload.selections:
        row = (
            schedule_index.loc[selection.event_id]
            if selection.event_id in schedule_index.index
            else None
        )
        event = event_map.get(selection.event_id)
        if event is None:
            event = {
                "event_id": selection.event_id,
                "date": selection.event_date,
                "event_label": selection.event_id,
                "data_state_date": (
                    None if state_date is None else state_date.strftime("%Y-%m-%d")
                ),
                "markets": [],
            }
        selections.append(
            _bb_selection_payload(event, selection, artifact, row)
        )

    _bb_mark_problems(selections)
    valid = [selection for selection in selections if selection["status"] == "ok"]
    same_game_groups = _bb_same_game_groups(valid)
    if not selections:
        joint_status = "empty"
        joint_probability = None
        joint_reason = "El ticket está vacío."
    elif len(valid) != len(selections):
        joint_status = "invalid"
        joint_probability = None
        joint_reason = (
            "No se calcula la probabilidad conjunta porque hay selecciones "
            "desactualizadas, inválidas o incompatibles."
        )
    elif same_game_groups:
        joint_status = "same_game_dependency"
        joint_probability = None
        joint_reason = (
            "Hay selecciones del mismo partido. No se multiplican como si "
            "fueran independientes porque no existe un modelo de dependencia "
            "validado para combinarlas."
        )
    else:
        joint_status = "independence"
        joint_probability = combine_independent(
            [selection["probability"] for selection in valid]
        )
        joint_reason = (
            "Se aplica un supuesto explícito de independencia entre partidos "
            "distintos. No modela correlación por noticias, lesiones o contexto."
        )

    fair_odds = None
    if joint_probability is not None and joint_probability > 0:
        fair_odds = fair_decimal_odds(joint_probability)
    market_combined_odds = (
        _bb_odds_product(valid) if joint_status == "independence" else None
    )

    requested_odds = None
    warnings: list[str] = []
    if payload.decimal_odds is not None:
        try:
            requested_odds = validate_decimal_odds(payload.decimal_odds)
        except ValueError as error:
            warnings.append(str(error))
    odds_used = requested_odds if requested_odds is not None else market_combined_odds
    odds_source = (
        "user"
        if requested_odds is not None
        else ("market" if market_combined_odds is not None else None)
    )
    stake = validate_stake(payload.stake)
    potential = (
        potential_return(stake, odds_used) if odds_used is not None else None
    )
    ev = (
        expected_value(joint_probability, odds_used)
        if joint_probability is not None and odds_used is not None
        else None
    )

    if state_date is not None:
        state_age_days = max(0, (_today() - state_date).days)
        if state_age_days > 7:
            warnings.append(
                f"El estado de los equipos es del {state_date.date()} "
                f"({state_age_days} días); la predicción puede estar desactualizada."
            )
    else:
        state_age_days = None
    for selection in selections:
        if selection["status"] != "ok":
            warnings.append(
                f"{selection.get('event_label') or selection['event_id']}: "
                f"{selection.get('stale_reason') or 'selección no válida'}"
            )
    if same_game_groups:
        warnings.append(
            "Probabilidad conjunta no disponible: hay selecciones del mismo "
            "partido y no se modela su dependencia."
        )
    if market_combined_odds is None and valid:
        warnings.append(
            "No todas las selecciones tienen cuota de mercado. La cuota "
            "combinada de mercado y el EV solo se calculan si introduces una "
            "cuota decimal o si todas las selecciones tienen cuota verificada."
        )
    if requested_odds is not None and fair_odds is not None:
        if requested_odds > fair_odds:
            warnings.append(
                "La cuota introducida es mayor que la cuota justa teórica del "
                "modelo. Esto no garantiza valor positivo: revisa la "
                "incertidumbre y el margen de la casa."
            )
    if (
        requested_odds is None
        and market_combined_odds is not None
        and fair_odds is not None
        and market_combined_odds > fair_odds
    ):
        warnings.append(
            "La cuota combinada de mercado es mayor que la cuota justa teórica "
            "del modelo. El EV positivo depende de que las probabilidades del "
            "modelo sean correctas; no es una garantía de beneficio."
        )

    odds_as_of = next(
        (
            selection.get("odds_as_of")
            for selection in selections
            if selection.get("odds_as_of")
        ),
        None,
    )
    data_quality = {
        "state_date": None if state_date is None else state_date.strftime("%Y-%m-%d"),
        "state_age_days": state_age_days,
        "odds_source": "ESPN / DraftKings" if odds_as_of else None,
        "odds_as_of": odds_as_of,
        "selections_with_market_odds": sum(
            1 for selection in selections if selection.get("decimal_odds") is not None
        ),
        "custom_lines": sum(
            1 for selection in selections if selection.get("is_custom_line")
        ),
        "same_game_groups": len(same_game_groups),
    }
    assumptions = [
        "Las probabilidades individuales son frecuencias empíricas de los "
        "residuos walk-forward del modelo Ridge existente.",
        (
            "La combinación usa el supuesto de independencia entre partidos."
            if joint_status == "independence"
            else (
                "No se aplica independencia a selecciones del mismo partido."
                if joint_status == "same_game_dependency"
                else "No se calcula una probabilidad conjunta; no se aplica el supuesto de independencia."
            )
        ),
        "No se incluyen lesiones, alineaciones, noticias de última hora ni "
        "cambios de cuota posteriores a la consulta.",
    ]
    return _ok(
        {
            "generated_at": pd.Timestamp.now(tz=NBA_ET).strftime("%Y-%m-%d %H:%M ET"),
            "count": len(selections),
            "selections": selections,
            "joint_status": joint_status,
            "joint_probability": _rounded(joint_probability, 8),
            "joint_reason": joint_reason,
            "same_game_groups": same_game_groups,
            "fair_decimal_odds": _rounded(fair_odds, 6),
            "market_combined_decimal_odds": _rounded(market_combined_odds, 6),
            "market_combined_available": market_combined_odds is not None,
            "decimal_odds": (
                None if odds_used is None else _rounded(odds_used, 6)
            ),
            "decimal_odds_source": odds_source,
            "stake": _rounded(stake, 2),
            "potential_return": _rounded(potential, 4),
            "ev": _rounded(ev, 8),
            "warnings": warnings,
            "assumptions": assumptions,
            "limitations": BET_BUILDER_LIMITATIONS,
            "data_quality": data_quality,
        }
    )


@app.get("/api/predict")
def predict(
    home: str,
    away: str,
    date: str | None = None,
    line: float = Query(225.5, ge=100, le=350),
    handicap: float | None = Query(None, ge=-60, le=60),
) -> JSONResponse:
    artifact = _artifact()
    state = _state()
    as_of = _today(date)
    try:
        result = predict_game(
            artifact,
            state,
            home=home,
            away=away,
            as_of=as_of,
            line=line,
            handicap=handicap,
            warn_stale=False,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    names = _team_names(state)
    home_name = names.get(str(result["home"]), str(result["home"]))
    away_name = names.get(str(result["away"]), str(result["away"]))
    result["home_name"] = home_name
    result["away_name"] = away_name
    result["lean"] = _lean(result.get("probability_over"))
    result["total_label"] = _total_label(result["lean"], line)
    result.update(_handicap_fields(result, home_name, away_name, None))
    result["analysis"] = _analysis_text(result, home_name, away_name, line)
    result["warning"] = _prediction_warning(result, as_of)
    return _ok(result)


@app.get("/api/players")
def players(
    season: str = "2025-26",
    mode: str = "pergame",
    sort: str = "pra",
    limit: int = Query(25, ge=1, le=100),
    team: str | None = None,
    min_gp: int = Query(1, ge=1, le=82),
    q: str | None = None,
) -> JSONResponse:
    if season not in AVAILABLE_SEASONS:
        raise HTTPException(
            status_code=404, detail=f"Temporada no disponible: {season}"
        )
    mode_key = mode.strip().lower()
    if mode_key not in PLAYER_MODES:
        raise HTTPException(status_code=400, detail="mode debe ser pergame o totals")
    sort_key = sort.strip().lower()
    if sort_key not in SORT_COLUMNS:
        raise HTTPException(
            status_code=400, detail=f"sort debe ser uno de {sorted(SORT_COLUMNS)}"
        )

    frame = _players(season, mode_key)
    if team:
        selected = [item.strip().upper() for item in team.split(",") if item.strip()]
        if selected:
            frame = frame[frame["team"].isin(selected)]
    if q and q.strip():
        frame = frame[
            frame["player_name"].str.contains(q.strip(), case=False, regex=False)
        ]
    frame = frame[frame["gp"] >= min_gp]
    total = len(frame)
    frame = frame.sort_values([sort_key, "pra"], ascending=False)

    fields = [
        "player_id",
        "player_name",
        "team",
        "gp",
        "min",
        "pts",
        "reb",
        "ast",
        "pra",
    ]
    rows = []
    for record in frame.head(limit)[fields].to_dict(orient="records"):
        player_id = record.get("player_id")
        if player_id is not None and not pd.isna(player_id):
            record["headshot_url"] = (
                "https://cdn.nba.com/headshots/nba/latest/260x190/"
                f"{int(player_id)}.png"
            )
        else:
            record["headshot_url"] = None
        rows.append(record)
    return _ok(
        {
            "season": season,
            "mode": mode_key,
            "sort": sort_key,
            "count": total,
            "players": rows,
        }
    )


@app.get("/api/player-props")
def player_props(
    days: int = Query(7, ge=1, le=14),
    team: str | None = None,
    today: str | None = None,
    limit: int = Query(10, ge=1, le=20),
) -> JSONResponse:
    artifact = _player_props()
    player_state = _player_state()
    state = _state()
    schedule = _schedule()
    start = _today(today)
    end = start + pd.Timedelta(days=days - 1)

    dates = pd.to_datetime(schedule["game_date"]).dt.normalize()
    mask = dates.between(start, end) & schedule["status"].eq(1)
    selected = [item.strip().upper() for item in (team or "").split(",") if item.strip()]
    if selected:
        mask &= schedule["home"].isin(selected) | schedule["away"].isin(selected)
    games = schedule.loc[mask].sort_values(["game_date", "game_time_et", "game_id"])

    available_teams = set(player_state["team"].astype(str).str.upper())
    scheduled_teams = set(games["home"]) | set(games["away"])
    teams_without_games = sorted(scheduled_teams - available_teams)

    names = _team_names(state)
    payload_games: list[dict[str, Any]] = []
    player_count = 0
    for game in games.itertuples(index=False):
        players = predict_player_props(
            artifact,
            player_state,
            state,
            home=str(game.home),
            away=str(game.away),
            game_date=pd.Timestamp(game.game_date),
            limit=limit,
        )
        if not players:
            continue
        player_count += len(players)
        payload_games.append(
            {
                "game_id": game.game_id,
                "date": pd.Timestamp(game.game_date).strftime("%Y-%m-%d"),
                "time_et": None if pd.isna(game.game_time_et) else str(game.game_time_et),
                "home": game.home,
                "away": game.away,
                "home_name": names.get(str(game.home), str(game.home)),
                "away_name": names.get(str(game.away), str(game.away)),
                "players": players,
            }
        )

    last_state = pd.to_datetime(player_state["last_game_date"]).max().normalize()
    team_state_date = pd.to_datetime(state["last_game_date"]).max().normalize()
    stale_days = max(0, (start - last_state).days)
    warning = None
    if stale_days > 7:
        warning = (
            f"La foto de jugadores es del {last_state.date()} ({stale_days} días). "
            "Las proyecciones no incorporan partidos posteriores ni lesiones."
        )
    metrics = artifact.get("metrics", {})
    return _ok(
        {
            "season": _season_for(start),
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
            "count": len(payload_games),
            "player_count": player_count,
            "limit": limit,
            "player_state_date": last_state.strftime("%Y-%m-%d"),
            "team_state_date": team_state_date.strftime("%Y-%m-%d"),
            "teams_without_games": teams_without_games,
            "model_name": artifact.get("model_name"),
            "trained_through": artifact.get("trained_through"),
            "metrics": metrics.get("targets"),
            "pra_metrics": metrics.get("pra"),
            "validation": metrics.get("validation"),
            "warning": warning,
            "grade_note": (
                "Nivel A-D: confianza del modelo sobre PRA (1 - sigma/PRA). "
                "A 80-100%, B 65-80%, C 50-65%, D menos de 50%; no es una "
                "probabilidad de apuesta."
            ),
            "note": (
                "Proyecciones por partido con un modelo Ridge entrenado con "
                "game logs 2023-24 a 2026-27 y validado fuera de muestra en "
                "2025-26. Usan medías móviles, descanso, localía y contexto "
                "del rival; el contexto de equipo es la última foto disponible. "
                "No incluyen lesiones, alineaciones ni cuotas de props; no es "
                "consejo de apuestas."
            ),
            "games": payload_games,
        }
    )


@app.get("/api/player-card")
def player_card(
    player_id: int = Query(..., ge=1),
    today: str | None = None,
) -> JSONResponse:
    """Ficha visual de un jugador: proyección, ultimos partidos y companeros."""
    artifact = _player_props()
    player_state = _player_state()
    team_state = _state()
    schedule = _schedule()
    history = _player_history()
    pergame = _players("2026-27", "pergame")
    bio = _player_bio()
    day = _today(today)

    state_rows = player_state[player_state["player_id"].astype(int).eq(player_id)]
    if state_rows.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No hay estado de props para el jugador {player_id}",
        )
    state_row = state_rows.iloc[0]
    team = str(state_row["team"]).upper().strip()

    names = _team_names(team_state)
    team_ids = _team_ids(team_state)
    team_name = names.get(team, team)

    pg_rows = pergame[pergame["player_id"].astype(int).eq(player_id)]
    pg_row = pg_rows.iloc[0] if not pg_rows.empty else None
    player_history = history[
        history["player_id"].astype(int).eq(player_id)
    ].sort_values(["game_date", "game_id"], ascending=False)
    current_history = player_history[
        player_history["season"].astype(str).eq("2026-27")
    ]
    usage_sample = current_history if not current_history.empty else player_history
    usage = _mean_value(usage_sample, "usage")
    usage_gp = int(len(usage_sample))
    if pg_row is not None:
        gp = int(pg_row["gp"])
        season_fgm = _rounded(pg_row.get("fgm"), 3) or 0.0
        season_fga = _rounded(pg_row.get("fga"), 3) or 0.0
        season_fg3m = _rounded(pg_row.get("fg3m"), 3) or 0.0
        season_fta = _rounded(pg_row.get("fta"), 3) or 0.0
        season_pts = _rounded(pg_row.get("pts"), 3) or 0.0
        season_ts = (
            season_pts / (2 * (season_fga + 0.44 * season_fta))
            if season_fga or season_fta
            else None
        )
        season_efg = (
            (season_fgm + 0.5 * season_fg3m) / season_fga
            if season_fga
            else None
        )
        averages = {
            "min": _rounded(pg_row.get("min")),
            "pts": _rounded(pg_row.get("pts")),
            "reb": _rounded(pg_row.get("reb")),
            "ast": _rounded(pg_row.get("ast")),
            "pra": _rounded(pg_row.get("pra")),
            "stl": _rounded(pg_row.get("stl")),
            "blk": _rounded(pg_row.get("blk")),
            "fg3m": _rounded(pg_row.get("fg3m")),
            "fga": _rounded(pg_row.get("fga")),
            "fta": _rounded(pg_row.get("fta")),
            "tov": _rounded(pg_row.get("tov")),
            "fg_pct": _rounded(pg_row.get("fg_pct"), 3),
            "fg3_pct": _rounded(pg_row.get("fg3_pct"), 3),
            "ft_pct": _rounded(pg_row.get("ft_pct"), 3),
            "ts_pct": _rounded(season_ts, 3),
            "efg_pct": _rounded(season_efg, 3),
            "usage": usage,
        }
    else:
        gp = int(state_row["gp_season"])
        pts = _rounded(state_row.get("pts_roll10")) or 0.0
        reb = _rounded(state_row.get("reb_roll10")) or 0.0
        ast = _rounded(state_row.get("ast_roll10")) or 0.0
        averages = {
            "min": _rounded(state_row.get("min_roll10")),
            "pts": pts,
            "reb": reb,
            "ast": ast,
            "pra": round(pts + reb + ast, 1),
            "stl": None,
            "blk": None,
            "fg3m": None,
            "fga": None,
            "fta": None,
            "tov": None,
            "fg_pct": None,
            "fg3_pct": None,
            "ft_pct": None,
            "ts_pct": None,
            "efg_pct": None,
            "usage": usage,
        }

    efficiency = {
        "fg_pct": averages.get("fg_pct"),
        "fg3_pct": averages.get("fg3_pct"),
        "ft_pct": averages.get("ft_pct"),
        "ts_pct": averages.get("ts_pct"),
        "efg_pct": averages.get("efg_pct"),
        "usage": usage,
        "usage_gp": usage_gp,
        "fgm": _rounded(pg_row.get("fgm"), 3) if pg_row is not None else None,
        "fga": _rounded(pg_row.get("fga"), 3) if pg_row is not None else None,
        "fg3m": _rounded(pg_row.get("fg3m"), 3) if pg_row is not None else None,
        "fg3a": _rounded(pg_row.get("fg3a"), 3) if pg_row is not None else None,
        "ftm": _rounded(pg_row.get("ftm"), 3) if pg_row is not None else None,
        "fta": _rounded(pg_row.get("fta"), 3) if pg_row is not None else None,
    }

    dates = pd.to_datetime(schedule["game_date"]).dt.normalize()
    mask = (
        schedule["status"].eq(1)
        & dates.ge(day)
        & (schedule["home"].eq(team) | schedule["away"].eq(team))
    )
    team_games = schedule.loc[mask].sort_values(
        ["game_date", "game_time_et", "game_id"]
    )

    upcoming = None
    teammates: list[dict[str, Any]] = []
    if not team_games.empty:
        game = team_games.iloc[0]
        home = str(game["home"]).upper()
        away = str(game["away"]).upper()
        game_date = pd.Timestamp(game["game_date"])
        predictions = predict_player_props(
            artifact,
            player_state,
            team_state,
            home=home,
            away=away,
            game_date=game_date,
            limit=80,
        )
        projection = None
        found = next(
            (item for item in predictions if int(item["player_id"]) == player_id),
            None,
        )
        if found:
            projection = {
                "min": found.get("projected_min"),
                "pts": found.get("projected_pts"),
                "reb": found.get("projected_reb"),
                "ast": found.get("projected_ast"),
                "pra": found.get("projected_pra"),
            }
        teammates = [
            {
                **item,
                "team_logo_url": _team_logo_url(team_ids.get(str(item["team"]).upper())),
                "recent_pra": None,
            }
            for item in predictions
            if str(item["team"]).upper() == team
            and int(item["player_id"]) != player_id
        ][:5]
        upcoming = {
            "game_id": str(game["game_id"]),
            "date": game_date.strftime("%Y-%m-%d"),
            "time_et": None
            if pd.isna(game["game_time_et"])
            else str(game["game_time_et"]),
            "home": home,
            "away": away,
            "home_name": names.get(home, home),
            "away_name": names.get(away, away),
            "opponent": away if home == team else home,
            "opponent_logo_url": _team_logo_url(
                team_ids.get(away if home == team else home)
            ),
            "is_home": home == team,
            "projection": projection,
        }

    if not teammates:
        peers = player_state[
            player_state["team"].astype(str).str.upper().eq(team)
            & player_state["player_id"].astype(int).ne(player_id)
        ].sort_values("pra_roll5", ascending=False).head(5)
        teammates = [
            {
                "player_id": int(row.player_id),
                "player_name": str(row.player_name),
                "team": str(row.team).upper(),
                "projected_pra": None,
                "projected_pts": None,
                "recent_pra": _rounded(row.pra_roll5),
                "headshot_url": (
                    "https://cdn.nba.com/headshots/nba/latest/260x190/"
                    f"{int(row.player_id)}.png"
                ),
                "team_logo_url": _team_logo_url(team_ids.get(team)),
            }
            for row in peers.itertuples(index=False)
        ]

    games = [
        _history_game_payload(row, team_ids)
        for row in player_history.head(15).itertuples(index=False)
    ]

    h2h = None
    likely_defender = None
    defenders: list[dict[str, Any]] = []
    if upcoming:
        opponent = str(upcoming["opponent"]).upper().strip()
        h2h_rows = player_history[
            player_history["opponent"].astype(str).str.upper().str.strip().eq(opponent)
        ].sort_values(["game_date", "game_id"], ascending=False)
        h2h_shooting = _shooting_from_box(h2h_rows)
        h2h_games = int(len(h2h_rows))
        wins = int(
            h2h_rows["wl"].astype(str).str.upper().eq("W").sum()
        ) if h2h_games else 0
        losses = int(
            h2h_rows["wl"].astype(str).str.upper().eq("L").sum()
        ) if h2h_games else 0
        h2h = {
            "opponent": opponent,
            "opponent_name": names.get(opponent, opponent),
            "opponent_logo_url": upcoming.get("opponent_logo_url"),
            "is_home": upcoming.get("is_home"),
            "games": h2h_games,
            "wins": wins,
            "losses": losses,
            "sample_size": int(len(player_history)),
            "averages": {
                "min": _mean_value(h2h_rows, "min"),
                "pts": _mean_value(h2h_rows, "pts"),
                "reb": _mean_value(h2h_rows, "reb"),
                "ast": _mean_value(h2h_rows, "ast"),
                "pra": _mean_value(h2h_rows, "pra"),
                "usage": _mean_value(h2h_rows, "usage"),
                "fg_pct": h2h_shooting["fg_pct"],
                "fg3_pct": h2h_shooting["fg3_pct"],
                "ft_pct": h2h_shooting["ft_pct"],
                "ts_pct": h2h_shooting["ts_pct"],
            },
            "meetings": [
                _history_game_payload(row, team_ids)
                for row in h2h_rows.head(5).itertuples(index=False)
            ],
        }

        defender_groups = pd.DataFrame()
        defender_frame = _player_defenders()
        if not defender_frame.empty:
            defender_rows = defender_frame[
                defender_frame["off_player_id"].astype(int).eq(player_id)
            ].copy()
            if not defender_rows.empty:
                for column in [
                    "gp",
                    "matchup_min",
                    "player_pts",
                    "matchup_ast",
                    "matchup_tov",
                    "matchup_blk",
                    "matchup_fgm",
                    "matchup_fga",
                    "matchup_fg3m",
                    "matchup_fg3a",
                    "matchup_ftm",
                    "matchup_fta",
                ]:
                    defender_rows[column] = pd.to_numeric(
                        defender_rows[column], errors="coerce"
                    ).fillna(0)
                defender_groups = (
                    defender_rows.groupby(
                        ["def_player_id", "def_player_name"], as_index=False
                    )
                    .agg(
                        team=("def_team", "first"),
                        gp=("gp", "sum"),
                        matchup_min=("matchup_min", "sum"),
                        player_pts=("player_pts", "sum"),
                        matchup_ast=("matchup_ast", "sum"),
                        matchup_tov=("matchup_tov", "sum"),
                        matchup_blk=("matchup_blk", "sum"),
                        matchup_fgm=("matchup_fgm", "sum"),
                        matchup_fga=("matchup_fga", "sum"),
                        matchup_fg3m=("matchup_fg3m", "sum"),
                        matchup_fg3a=("matchup_fg3a", "sum"),
                        matchup_ftm=("matchup_ftm", "sum"),
                        matchup_fta=("matchup_fta", "sum"),
                    )
                    .sort_values("matchup_min", ascending=False)
                )
                grouped = defender_groups.head(5)
                for row in grouped.itertuples(index=False):
                    def_team = None if pd.isna(row.team) else str(row.team).upper()
                    fga = float(row.matchup_fga)
                    fg3a = float(row.matchup_fg3a)
                    fta = float(row.matchup_fta)
                    gp_value = max(1.0, float(row.gp))
                    defenders.append(
                        {
                            "player_id": int(row.def_player_id),
                            "player_name": str(row.def_player_name),
                            "team": def_team,
                            "team_name": names.get(def_team, def_team)
                            if def_team
                            else None,
                            "team_logo_url": _team_logo_url(
                                team_ids.get(def_team)
                            )
                            if def_team
                            else None,
                            "headshot_url": (
                                "https://cdn.nba.com/headshots/nba/latest/260x190/"
                                f"{int(row.def_player_id)}.png"
                            ),
                            "gp": int(row.gp),
                            "matchup_min": _rounded(row.matchup_min, 1),
                            "matchup_min_pg": _rounded(
                                row.matchup_min / gp_value, 1
                            ),
                            "player_pts": _rounded(row.player_pts, 1),
                            "player_pts_pg": _rounded(
                                row.player_pts / gp_value, 1
                            ),
                            "fg_pct": _rounded(
                                row.matchup_fgm / fga, 3
                            )
                            if fga
                            else None,
                            "fg3_pct": _rounded(
                                row.matchup_fg3m / fg3a, 3
                            )
                            if fg3a
                            else None,
                            "ft_pct": _rounded(
                                row.matchup_ftm / fta, 3
                            )
                            if fta
                            else None,
                            "is_next_opponent": def_team == opponent,
                        }
                    )

        likely_defender = _likely_defender_payload(
            player_id,
            opponent,
            defender_groups,
            pergame,
            pg_row,
            names,
            team_ids,
            bio,
        )

    last_state = pd.to_datetime(player_state["last_game_date"]).max().normalize()
    player = {
        "player_id": player_id,
        "player_name": str(state_row["player_name"]),
        "team": team,
        "team_name": team_name,
        "team_logo_url": _team_logo_url(team_ids.get(team)),
        "headshot_url": (
            "https://cdn.nba.com/headshots/nba/latest/260x190/"
            f"{player_id}.png"
        ),
        "season": "2026-27",
        "gp": gp,
        "averages": averages,
    }
    return _ok(
        {
            "season": "2026-27",
            "player": player,
            "efficiency": efficiency,
            "upcoming": upcoming,
            "h2h": h2h,
            "likely_defender": likely_defender,
            "defenders": defenders,
            "games": games,
            "teammates": teammates,
            "model_name": artifact.get("model_name"),
            "player_state_date": last_state.strftime("%Y-%m-%d"),
            "note": (
                "Las barras verdes superan la línea y las rojas no. La fila "
                "PROX. es una proyección del modelo; el resto son partidos "
                "reales. El H2H y los defensores usan la muestra reciente; no "
                "son una asignacion defensiva para el proximo partido. No "
                "incluye lesiones, alineaciones ni cuotas de props; no es "
                "consejo de apuestas."
            ),
        }
    )


@app.get("/api/results")
def results(
    season: str = "2025-26",
    line: float = Query(225.5, ge=100, le=350),
    limit: int = Query(25, ge=1, le=200),
    team: str | None = None,
    today: str | None = None,
    live: bool = False,
) -> JSONResponse:
    artifact = _artifact()
    day = _today(today)
    live_updated = None
    names = _team_names(_state())

    if season == "2025-26":
        frame = _test_predictions()
        source = "Predicciones fuera de muestra de 2025-26"
    elif season == _season_for(day):
        schedule = _schedule()
        if live:
            schedule, live_updated = _merge_live(schedule, _live_dates(today))
        finals = schedule[
            schedule["status"].eq(3) & schedule["total_points"].notna()
        ].copy()
        state = _state()
        rows: list[dict[str, Any]] = []
        for game in finals.itertuples(index=False):
            try:
                result = predict_game(
                    artifact,
                    state,
                    home=game.home,
                    away=game.away,
                    as_of=pd.Timestamp(game.game_date),
                    line=line,
                    warn_stale=False,
                )
            except ValueError:
                continue
            home_name = names.get(str(game.home), str(game.home))
            away_name = names.get(str(game.away), str(game.away))
            actual_margin = float(game.home_score) - float(game.away_score)
            predicted_margin = result.get("predicted_margin")
            handicap_info = _handicap_fields(result, home_name, away_name, None)
            rows.append(
                {
                    "game_date": pd.Timestamp(game.game_date),
                    "home_team": game.home,
                    "away_team": game.away,
                    "home_name": home_name,
                    "away_name": away_name,
                    "home_score": float(game.home_score),
                    "away_score": float(game.away_score),
                    "total_points": float(game.total_points),
                    "predicted_total": float(result["predicted_total"]),
                    "predicted_margin": predicted_margin,
                    "actual_margin": actual_margin,
                    "margin_error": (
                        float(predicted_margin) - actual_margin
                        if predicted_margin is not None
                        else None
                    ),
                    "possible_winner": handicap_info["possible_winner"],
                    "model_handicap": handicap_info["model_handicap"],
                    "actual_winner": home_name if actual_margin >= 0 else away_name,
                }
            )
        frame = pd.DataFrame(rows)
        source = f"Partidos finalizados de {season}"
        if live_updated:
            source += " · resultados en vivo"
    else:
        raise HTTPException(
            status_code=404, detail=f"Temporada no disponible: {season}"
        )

    if frame.empty:
        return _ok(
            {
                "season": season,
                "source": source,
                "line": line,
                "count": 0,
                "summary": summarize_results(
                    pd.DataFrame(columns=["total_points", "predicted_total", "hit"])
                ),
                "games": [],
                "live_updated": live_updated,
            }
        )

    frame = add_over_under(frame, line, artifact["residuals"])
    if team:
        selected = [item.strip().upper() for item in team.split(",") if item.strip()]
        if selected:
            frame = frame[
                frame["home_team"].isin(selected) | frame["away_team"].isin(selected)
            ]

    summary = summarize_results(frame)
    view = frame.sort_values("game_date", ascending=False).head(limit)
    games: list[dict[str, Any]] = []
    for row in view.itertuples(index=False):
        hit = None if pd.isna(row.hit) else int(float(row.hit) == 1.0)
        raw_home_name = getattr(row, "home_name", None)
        raw_away_name = getattr(row, "away_name", None)
        home_name = (
            str(raw_home_name)
            if raw_home_name is not None and not pd.isna(raw_home_name)
            else names.get(str(row.home_team), str(row.home_team))
        )
        away_name = (
            str(raw_away_name)
            if raw_away_name is not None and not pd.isna(raw_away_name)
            else names.get(str(row.away_team), str(row.away_team))
        )

        predicted_margin = getattr(row, "predicted_margin", None)
        if predicted_margin is not None and not pd.isna(predicted_margin):
            predicted_margin = float(predicted_margin)
            favorite = home_name if predicted_margin >= 0 else away_name
            handicap_line = round(abs(predicted_margin) * 2) / 2
            model_handicap = (
                f"{favorite} -{handicap_line:.1f}"
                if handicap_line >= 0.5
                else "Pick'em"
            )
            possible_winner = favorite
        else:
            predicted_margin = None
            model_handicap = getattr(row, "model_handicap", None)
            possible_winner = getattr(row, "possible_winner", None)

        actual_margin = getattr(row, "actual_margin", None)
        if actual_margin is not None and not pd.isna(actual_margin):
            actual_margin = float(actual_margin)
        else:
            actual_margin = None

        margin_error = getattr(row, "margin_error", None)
        if (
            margin_error is None
            and predicted_margin is not None
            and actual_margin is not None
        ):
            margin_error = predicted_margin - actual_margin
        if margin_error is not None and not pd.isna(margin_error):
            margin_error = float(margin_error)
        else:
            margin_error = None

        actual_winner = getattr(row, "actual_winner", None)
        if not actual_winner and actual_margin is not None:
            actual_winner = home_name if actual_margin >= 0 else away_name

        games.append(
            {
                "game_date": pd.Timestamp(row.game_date).strftime("%Y-%m-%d"),
                "home_team": row.home_team,
                "away_team": row.away_team,
                "total_points": row.total_points,
                "predicted_total": row.predicted_total,
                "error": float(row.predicted_total) - float(row.total_points),
                "line": line,
                "probability_over": row.probability_over,
                "actual_result": row.actual_result,
                "model_lean": row.model_lean,
                "hit": hit,
                "home_name": home_name,
                "away_name": away_name,
                "predicted_margin": predicted_margin,
                "actual_margin": actual_margin,
                "margin_error": margin_error,
                "possible_winner": possible_winner,
                "model_handicap": model_handicap,
                "actual_winner": actual_winner,
            }
        )

    return _ok(
        {
            "season": season,
            "source": source,
            "line": line,
            "count": int(len(frame)),
            "summary": summary,
            "games": games,
            "live_updated": live_updated,
            "caveat": (
                "La línea es la referencia introducida por el usuario. nba_api no "
                "proporciona líneas históricas de casas de apuestas, asi que esto "
                "no evalua el cierre real ni una rentabilidad de apuestas."
            ),
        }
    )


if PUBLIC_DIR.exists():
    # Debe ir al final: las rutas /api tienen prioridad sobre el mount estatico.
    app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")

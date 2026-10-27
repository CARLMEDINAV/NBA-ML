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
import sys
import time
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

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"
PUBLIC_DIR = ROOT / "public"

# Vercel no instala el paquete editable; con esto basta para importar src/.
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

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
ESPN_TO_NBA = {
    "GS": "GSW",
    "NO": "NOP",
    "NY": "NYK",
    "SA": "SAS",
    "UTAH": "UTA",
    "WSH": "WAS",
}

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
    allow_methods=["GET"],
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


def _prediction_warning(result: dict[str, Any], as_of: pd.Timestamp) -> str | None:
    oldest = min(
        pd.Timestamp(result["home_last_game"]),
        pd.Timestamp(result["away_last_game"]),
    )
    gap = (as_of - oldest).days
    if gap > 14:
        return (
            f"El ultimo partido de uno de los equipos fue hace {gap} dias. "
            "El estado de los equipos esta desactualizado; la prediccion "
            "asume descanso maximo."
        )
    return None


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
                "Sin lesiones ni alineaciones. El total y el hándicap son "
                "estimaciones del modelo; la línea de total es una referencia "
                "introducida por el usuario. Las cuotas de mercado, cuando "
                "aparecen, son informativas y no una recomendación de apuestas."
            ),
        }
    )


@app.get("/api/teams")
def teams() -> JSONResponse:
    return _ok({"teams": _team_payload(_state())})


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
            f"({stale_days} dias). Las predicciones no incorporan partidos "
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
            f"La foto de jugadores es del {last_state.date()} ({stale_days} dias). "
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
                "2025-26. Usan medias móviles, descanso, localía y contexto "
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
    """Ficha visual de un jugador: proyeccion, ultimos partidos y companeros."""
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
                "Las barras verdes superan la linea y las rojas no. La fila "
                "PROX. es una proyeccion del modelo; el resto son partidos "
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
                "La linea es la referencia introducida por el usuario. nba_api no "
                "proporciona lineas historicas de casas de apuestas, asi que esto "
                "no evalua el cierre real ni una rentabilidad de apuestas."
            ),
        }
    )


if PUBLIC_DIR.exists():
    # Debe ir al final: las rutas /api tienen prioridad sobre el mount estatico.
    app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")

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

from nba_ml.models.predict import load_artifact, load_team_state, predict_game
from nba_ml.models.results import add_over_under, summarize_results

AVAILABLE_SEASONS = ("2025-26", "2026-27")
PLAYER_MODES = {"pergame": "pergame", "totals": "totals"}
SORT_COLUMNS = {"pra", "pts", "reb", "ast", "min", "gp"}
LEAN_OVER = 0.55
LEAN_UNDER = 0.45
NBA_ET = ZoneInfo("America/New_York")
NBA_STATS_SCOREBOARD = "https://stats.nba.com/stats/scoreboardv3"
LIVE_TIMEOUT = 9.0
LIVE_CACHE_SECONDS = 25
LIVE_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
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
    description="Prediccion de totales NBA y estadisticas PRA (PTS + REB + AST).",
    version="0.2.0",
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
            "El estado de los equipos es una foto de 2025-26; la prediccion "
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
            "caveat": (
                "Sin lesiones, alineaciones ni cuotas. La linea es una referencia "
                "introducida por el usuario, no la linea de cierre."
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
    rows: list[dict[str, Any]] = []
    skipped = 0
    for game in games.itertuples(index=False):
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
            skipped += 1
            continue

        low, high = result["interval_80"]
        probability = float(result["probability_over"])
        rows.append(
            {
                "game_id": game.game_id,
                "date": pd.Timestamp(game.game_date).strftime("%Y-%m-%d"),
                "time_et": None if pd.isna(game.game_time_et) else str(game.game_time_et),
                "away": game.away,
                "home": game.home,
                "arena": getattr(game, "arena", None),
                "predicted_total": result["predicted_total"],
                "interval_low": low,
                "interval_high": high,
                "probability_over": probability,
                "lean": _lean(probability),
                "home_rest": result["home_rest"],
                "away_rest": result["away_rest"],
            }
        )

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
            "games": rows,
        }
    )


@app.get("/api/predict")
def predict(
    home: str,
    away: str,
    date: str | None = None,
    line: float = Query(225.5, ge=100, le=350),
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
            warn_stale=False,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    result["lean"] = _lean(result.get("probability_over"))
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

    fields = ["player_name", "team", "gp", "min", "pts", "reb", "ast", "pra"]
    rows = frame.head(limit)[fields].to_dict(orient="records")
    return _ok(
        {
            "season": season,
            "mode": mode_key,
            "sort": sort_key,
            "count": total,
            "players": rows,
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
        rows: list[dict[str, Any]] = []
        for game in finals.itertuples(index=False):
            try:
                result = predict_game(
                    artifact,
                    _state(),
                    home=game.home,
                    away=game.away,
                    as_of=pd.Timestamp(game.game_date),
                    line=line,
                    warn_stale=False,
                )
            except ValueError:
                continue
            rows.append(
                {
                    "game_date": pd.Timestamp(game.game_date),
                    "home_team": game.home,
                    "away_team": game.away,
                    "total_points": float(game.total_points),
                    "predicted_total": float(result["predicted_total"]),
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

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
from functools import lru_cache
from pathlib import Path
from typing import Any

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
        return pd.Timestamp.today().normalize()
    try:
        return pd.Timestamp(value).normalize()
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=400, detail="Fecha no valida; usa YYYY-MM-DD"
        ) from error


def _season_for(day: pd.Timestamp) -> str:
    year = day.year if day.month >= 9 else day.year - 1
    return f"{year}-{str(year + 1)[-2:]}"


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
) -> JSONResponse:
    artifact = _artifact()
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
) -> JSONResponse:
    artifact = _artifact()
    day = _today(today)

    if season == "2025-26":
        frame = _test_predictions()
        source = "Predicciones fuera de muestra de 2025-26"
    elif season == _season_for(day):
        schedule = _schedule()
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

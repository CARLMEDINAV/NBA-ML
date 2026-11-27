"""API del modulo PROP LAB: tendencias descriptivas sobre game logs publicados."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"
HISTORY_PATH = ARTIFACTS / "player_game_logs_recent.csv"

router = APIRouter(prefix="/api/prop-lab", tags=["PROP LAB"])

MARKETS = {
    "PTS": ("pts", "Puntos"),
    "REB": ("reb", "Rebotes"),
    "AST": ("ast", "Asistencias"),
    "3PM": ("fg3m", "Triples anotados"),
    "PRA": ("pra", "Puntos + rebotes + asistencias"),
}


def _history_mtime() -> float:
    return HISTORY_PATH.stat().st_mtime if HISTORY_PATH.exists() else 0.0


@lru_cache(maxsize=2)
def _load_history(mtime: float) -> pd.DataFrame:
    del mtime
    if not HISTORY_PATH.exists():
        return pd.DataFrame()
    frame = pd.read_csv(
        HISTORY_PATH,
        dtype={"player_id": "Int64", "game_id": str},
        parse_dates=["game_date"],
    )
    required = {"player_id", "player_name", "team", "game_id", "game_date", "min", *[v[0] for v in MARKETS.values()]}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Historial de jugadores incompleto: {sorted(missing)}")
    for column in ["min", *[v[0] for v in MARKETS.values()]]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["player_id"] = pd.to_numeric(frame["player_id"], errors="coerce")
    frame = frame.dropna(subset=["player_id", "game_date"])
    frame["player_id"] = frame["player_id"].astype(int)
    frame["team"] = frame["team"].astype(str).str.upper().str.strip()
    return frame.sort_values(["player_id", "game_date", "game_id"])


def _history() -> pd.DataFrame:
    try:
        return _load_history(_history_mtime()).copy()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail="Aún no está publicado artifacts/player_game_logs_recent.csv. Ejecuta el workflow de actualización de props.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _window_summary(values: list[float], line: float | None) -> dict[str, Any]:
    clean = [float(value) for value in values if np.isfinite(value)]
    result: dict[str, Any] = {
        "sample": len(clean),
        "average": round(float(np.mean(clean)), 1) if clean else None,
        "values": [round(value, 1) for value in clean],
    }
    if line is not None:
        over = sum(value > line for value in clean)
        under = sum(value < line for value in clean)
        push = sum(value == line for value in clean)
        valid = over + under
        result.update({
            "line": line,
            "over": over,
            "under": under,
            "push": push,
            "valid": valid,
            "over_rate": round(over / valid, 4) if valid else None,
            "under_rate": round(under / valid, 4) if valid else None,
        })
    return result


@router.get("/health")
def prop_lab_health() -> dict[str, Any]:
    exists = HISTORY_PATH.exists()
    return {
        "module": "PROP LAB",
        "status": "ready" if exists else "waiting_for_history",
        "history_available": exists,
        "history_file": HISTORY_PATH.name,
        "last_modified": pd.Timestamp(HISTORY_PATH.stat().st_mtime, unit="s", tz="UTC").isoformat() if exists else None,
        "markets": list(MARKETS),
        "odds_source": "existing_only",
        "note": "No se generan cuotas de props. Las tendencias son frecuencias históricas, no probabilidades futuras.",
    }


@router.get("/trends")
def prop_lab_trends(
    market: str = Query("PTS", pattern="^(PTS|REB|AST|3PM|PRA)$"),
    season: str | None = Query("2026-27"),
    window: int = Query(10, ge=5, le=15),
    line: float | None = Query(None, ge=0, le=100),
    side: str = Query("ALL", pattern="^(ALL|OVER|UNDER)$"),
    min_sample: int = Query(1, ge=1, le=15),
    team: str | None = None,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=300),
) -> dict[str, Any]:
    frame = _history()
    market_key = market.upper()
    stat, label = MARKETS[market_key]
    if season and "season" in frame.columns:
        frame = frame[frame["season"].astype(str).eq(season)]
    if team:
        teams = [item.strip().upper() for item in team.split(",") if item.strip()]
        frame = frame[frame["team"].isin(teams)]
    if q and q.strip():
        frame = frame[frame["player_name"].astype(str).str.contains(q.strip(), case=False, regex=False)]
    frame = frame[frame["min"].fillna(0).gt(0) & frame[stat].notna()]
    rows: list[dict[str, Any]] = []
    for player_id, group in frame.groupby("player_id", sort=False):
        group = group.sort_values(["game_date", "game_id"]).tail(window)
        values = pd.to_numeric(group[stat], errors="coerce").dropna().astype(float).tolist()
        if len(values) < min_sample:
            continue
        summary = _window_summary(values, line)
        if line is not None and side == "OVER" and not summary["over"]:
            continue
        if line is not None and side == "UNDER" and not summary["under"]:
            continue
        last = group.iloc[-1]
        rows.append({
            "player_id": int(player_id),
            "player_name": str(last["player_name"]),
            "team": str(last["team"]),
            "market": market_key,
            "market_label": label,
            "window": window,
            "last_game_date": pd.Timestamp(last["game_date"]).strftime("%Y-%m-%d"),
            "headshot_url": f"https://cdn.nba.com/headshots/nba/latest/260x190/{int(player_id)}.png",
            **summary,
        })
    rows.sort(
        key=lambda item: (
            item.get("over_rate") if line is not None and side == "OVER" else
            item.get("under_rate") if line is not None and side == "UNDER" else
            item["average"]
        ) if item.get("average") is not None else -1,
        reverse=True,
    )
    return {
        "market": market_key,
        "season": season,
        "window": window,
        "line": line,
        "side": side,
        "count": len(rows),
        "players": rows[:limit],
        "data_as_of": max((item["last_game_date"] for item in rows), default=None),
        "note": "La frecuencia se calcula sobre partidos válidos sin contar pushes. No equivale a probabilidad futura ni a valor esperado.",
    }


@router.get("/player/{player_id}")
def prop_lab_player(
    player_id: int,
    market: str = Query("PTS", pattern="^(PTS|REB|AST|3PM|PRA)$"),
    season: str | None = Query("2026-27"),
    line: float | None = Query(None, ge=0, le=100),
) -> dict[str, Any]:
    frame = _history()
    if season and "season" in frame.columns:
        frame = frame[frame["season"].astype(str).eq(season)]
    frame = frame[frame["player_id"].eq(player_id)].sort_values(["game_date", "game_id"])
    if frame.empty:
        raise HTTPException(status_code=404, detail="No hay historial para este jugador y temporada.")
    stat, label = MARKETS[market.upper()]
    frame = frame[frame["min"].fillna(0).gt(0) & frame[stat].notna()].tail(15)
    values = frame[stat].astype(float).tolist()
    return {
        "player_id": player_id,
        "player_name": str(frame.iloc[-1]["player_name"]),
        "team": str(frame.iloc[-1]["team"]),
        "market": market.upper(),
        "market_label": label,
        "line": line,
        "windows": {
            str(size): _window_summary(values[-size:], line)
            for size in (5, 10, 15)
        },
        "games": [
            {"date": pd.Timestamp(row.game_date).strftime("%Y-%m-%d"), "opponent": str(getattr(row, "opponent", "")), "value": round(float(getattr(row, stat)), 1), "minutes": round(float(row.min), 1)}
            for row in frame.itertuples(index=False)
        ],
        "note": "Muestra histórica descriptiva. Pushes no se cuentan como acierto ni fallo.",
    }

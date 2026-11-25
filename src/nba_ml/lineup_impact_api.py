"""Lineup Impact Simulator: transparent what-if adjustments over the existing Ridge baseline."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/lineup-impact", tags=["Lineup Impact Simulator"])


class PlayerIn(BaseModel):
    player_id: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1, max_length=80)
    minutes: float = Field(0, ge=0, le=48)
    pts: float = Field(0, ge=0, le=80)
    reb: float = Field(0, ge=0, le=35)
    ast: float = Field(0, ge=0, le=30)
    usage: float = Field(0, ge=0, le=100)
    position: str = Field("", max_length=8)
    availability_status: str = Field("UNKNOWN", max_length=24)


class TeamScenarioIn(BaseModel):
    team: str = Field(..., min_length=2, max_length=8)
    starters: list[PlayerIn] = Field(..., min_length=5, max_length=5)
    bench: list[PlayerIn] = Field(default_factory=list, max_length=12)
    removed_ids: list[str] = Field(default_factory=list, max_length=5)


class LineupImpactIn(BaseModel):
    home: TeamScenarioIn
    away: TeamScenarioIn
    baseline: dict[str, float]
    replacement_minutes_share: float = Field(0.72, ge=0.4, le=1.0)


def _position_match_score(starter_position: str, bench_position: str) -> int:
    """Preferir la misma posición; aceptar posiciones vecinas como fallback."""
    starter = starter_position.upper().strip()
    bench = bench_position.upper().strip()
    if not starter or not bench:
        return 1
    if starter == bench:
        return 3
    groups = ({"PG", "SG"}, {"SF", "PF"}, {"PF", "C"})
    if any(starter in group and bench in group for group in groups):
        return 2
    return 0


def _team_impact(team: TeamScenarioIn, replacement_share: float) -> dict[str, Any]:
    starters = {p.player_id: p for p in team.starters}
    removed = [
        player for player in team.starters
        if player.player_id in team.removed_ids
        or player.availability_status.upper() == "OUT"
    ]
    available_bench = [
        player for player in team.bench
        if player.availability_status.upper() != "OUT"
    ]
    excluded_bench = [
        player.name for player in team.bench
        if player.availability_status.upper() == "OUT"
    ]
    if not removed:
        return {
            "team": team.team.upper(), "removed": [], "replacements": [],
            "points_delta": 0.0, "rebounds_delta": 0.0, "assists_delta": 0.0,
            "usage_removed": 0.0, "available_bench_count": len(available_bench),
            "excluded_bench": excluded_bench,
        }

    unused_bench = list(available_bench)
    points_delta = rebounds_delta = assists_delta = usage_removed = 0.0
    replacements: list[dict[str, Any]] = []
    for player in removed:
        replacement = max(
            unused_bench,
            key=lambda candidate: (
                _position_match_score(player.position, candidate.position),
                candidate.minutes,
                candidate.pts,
            ),
            default=None,
        )
        if replacement is not None:
            unused_bench.remove(replacement)
        minutes = max(0.0, min(48.0, player.minutes))
        replacement_minutes = minutes * replacement_share if replacement else 0.0
        missing_rate = 1.0 / max(player.minutes, 1.0)
        replacement_rate = 1.0 / max(replacement.minutes, 1.0) if replacement else 0.0
        points_delta += (
            replacement_rate * replacement_minutes * replacement.pts
            if replacement else 0.0
        ) - missing_rate * minutes * player.pts
        rebounds_delta += (
            replacement_rate * replacement_minutes * replacement.reb
            if replacement else 0.0
        ) - missing_rate * minutes * player.reb
        assists_delta += (
            replacement_rate * replacement_minutes * replacement.ast
            if replacement else 0.0
        ) - missing_rate * minutes * player.ast
        usage_removed += player.usage
        replacements.append({
            "out": player.name,
            "in": replacement.name if replacement else "Rotación no identificada",
            "replacement_status": replacement.availability_status if replacement else None,
            "minutes_covered": round(replacement_minutes, 1),
            "position_match": _position_match_score(player.position, replacement.position) if replacement else 0,
        })

    return {
        "team": team.team.upper(),
        "removed": [player.name for player in removed],
        "replacements": replacements,
        "points_delta": round(points_delta, 2),
        "rebounds_delta": round(rebounds_delta, 2),
        "assists_delta": round(assists_delta, 2),
        "usage_removed": round(usage_removed, 2),
        "available_bench_count": len(available_bench),
        "excluded_bench": excluded_bench,
    }


@router.post("/simulate")
def simulate_lineup_impact(payload: LineupImpactIn) -> dict[str, Any]:
    home = _team_impact(payload.home, payload.replacement_minutes_share)
    away = _team_impact(payload.away, payload.replacement_minutes_share)
    base = payload.baseline
    home_score = float(base.get("home_score", 0)) + home["points_delta"]
    away_score = float(base.get("away_score", 0)) + away["points_delta"]
    total = home_score + away_score
    return {
        "baseline": {
            "home_score": round(float(base.get("home_score", 0)), 2),
            "away_score": round(float(base.get("away_score", 0)), 2),
            "total": round(float(base.get("total", 0)), 2),
        },
        "scenario": {
            "home_score": round(home_score, 2),
            "away_score": round(away_score, 2),
            "total": round(total, 2),
            "margin": round(home_score - away_score, 2),
        },
        "changes": {
            "home_points": home["points_delta"],
            "away_points": away["points_delta"],
            "total": round(home["points_delta"] + away["points_delta"], 2),
        },
        "teams": {"home": home, "away": away},
        "methodology": (
            "Estimación what-if: las bajas OUT confirmadas se excluyen automáticamente; "
            "cada titular ausente se empareja primero con un suplente disponible de la "
            "misma posición o una posición cercana, priorizando minutos y producción. "
            "El reemplazo absorbe la proporción configurada de minutos. QUESTIONABLE y "
            "DOUBTFUL se mantienen disponibles, pero se identifican como inciertos. "
            "No modela emparejamientos, defensa individual ni redistribución completa "
            "del uso; no es una probabilidad calibrada."
        ),
    }

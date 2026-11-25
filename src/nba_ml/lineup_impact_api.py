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


def _team_impact(team: TeamScenarioIn, replacement_share: float) -> dict[str, Any]:
    starters = {p.player_id: p for p in team.starters}
    removed = [starters[player_id] for player_id in team.removed_ids if player_id in starters]
    bench = sorted(team.bench, key=lambda p: (p.minutes, p.pts), reverse=True)
    if not removed:
        return {"team": team.team.upper(), "removed": [], "replacements": [], "points_delta": 0.0,
                "rebounds_delta": 0.0, "assists_delta": 0.0, "usage_removed": 0.0}
    replacement_pool = [p for p in bench if p.player_id not in team.removed_ids]
    points_delta = rebounds_delta = assists_delta = usage_removed = 0.0
    replacements = []
    for index, player in enumerate(removed):
        replacement = replacement_pool[index] if index < len(replacement_pool) else None
        minutes = max(0.0, min(48.0, player.minutes))
        # The replacement absorbs a configurable share of the missing minutes.
        share = replacement_share if replacement else 0.0
        replacement_minutes = minutes * share
        missing_rate = minutes / 36.0
        replacement_rate = replacement_minutes / 36.0
        points_delta += (replacement_rate * (replacement.pts if replacement else 0.0)) - (missing_rate * player.pts)
        rebounds_delta += (replacement_rate * (replacement.reb if replacement else 0.0)) - (missing_rate * player.reb)
        assists_delta += (replacement_rate * (replacement.ast if replacement else 0.0)) - (missing_rate * player.ast)
        usage_removed += player.usage
        replacements.append({
            "out": player.name,
            "in": replacement.name if replacement else "Rotación no identificada",
            "minutes_covered": round(replacement_minutes, 1),
        })
    return {
        "team": team.team.upper(),
        "removed": [p.name for p in removed],
        "replacements": replacements,
        "points_delta": round(points_delta, 2),
        "rebounds_delta": round(rebounds_delta, 2),
        "assists_delta": round(assists_delta, 2),
        "usage_removed": round(usage_removed, 2),
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
            "Estimación what-if basada en producción por 36 minutos y un reemplazo de la banca "
            "que absorbe la proporción configurada de los minutos. No modela emparejamientos, "
            "defensa individual ni redistribución completa del uso; no es una probabilidad calibrada."
        ),
    }

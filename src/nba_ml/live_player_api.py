"""ESPN live box-score endpoint for NBA Player Cards."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(tags=["Live Player Stats"])
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NBA-TOTALS/2.0)", "Accept": "application/json"}


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _parse_player_stats(summary: dict[str, Any], player_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    boxscore = summary.get("boxscore") or {}
    players = boxscore.get("players") or []
    for team_block in players:
        team = team_block.get("team") or {}
        for category in team_block.get("statistics") or []:
            names = [str(name).upper() for name in (category.get("names") or category.get("labels") or [])]
            for athlete_row in category.get("athletes") or []:
                athlete = athlete_row.get("athlete") or {}
                if str(athlete.get("id") or athlete_row.get("athleteId") or "") != player_id:
                    continue
                raw = athlete_row.get("stats") or []
                values = {name: raw[index] if index < len(raw) else None for index, name in enumerate(names)}
                fg = str(values.get("FG") or "")
                three = str(values.get("3PT") or values.get("3P") or "")
                ft = str(values.get("FT") or "")
                def made_attempted(pair: str) -> tuple[float | None, float | None]:
                    parts = pair.split("-", 1)
                    return (_number(parts[0]), _number(parts[1])) if len(parts) == 2 else (None, None)
                fgm, fga = made_attempted(fg)
                fg3m, fg3a = made_attempted(three)
                ftm, fta = made_attempted(ft)
                stats = {
                    "min": values.get("MIN"), "pts": _number(values.get("PTS")),
                    "reb": _number(values.get("REB")), "oreb": _number(values.get("OREB")),
                    "dreb": _number(values.get("DREB")), "ast": _number(values.get("AST")),
                    "stl": _number(values.get("STL")), "blk": _number(values.get("BLK")),
                    "tov": _number(values.get("TO") or values.get("TOV")),
                    "pf": _number(values.get("PF")), "fgm": fgm, "fga": fga,
                    "fg3m": fg3m, "fg3a": fg3a, "ftm": ftm, "fta": fta,
                    "plus_minus": _number(values.get("+/-") or values.get("PLUS/MINUS")),
                }
                return stats, {"id": str(team.get("id") or ""), "abbreviation": team.get("abbreviation"), "display_name": team.get("displayName")}
    return None, None


@router.get("/api/live/player-stats")
def live_player_stats(
    player_id: str = Query(..., min_length=1, max_length=20),
    event_id: str = Query(..., min_length=5, max_length=20),
) -> dict[str, Any]:
    """Return current ESPN box-score stats for one player in one live game."""
    try:
        response = httpx.get(
            ESPN_SUMMARY, params={"event": event_id}, headers=HEADERS,
            timeout=8.0, follow_redirects=True,
        )
        response.raise_for_status()
        summary = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo consultar el box score ESPN: {exc}") from exc

    stats, team = _parse_player_stats(summary, player_id)
    if stats is None:
        raise HTTPException(status_code=404, detail="El jugador todavía no aparece en el box score de este partido.")
    header = summary.get("header") or {}
    competitions = header.get("competitions") or []
    competition = competitions[0] if competitions else {}
    competitors = competition.get("competitors") or []
    score = {}
    for item in competitors:
        alias = (item.get("team") or {}).get("abbreviation")
        if alias:
            score[alias] = _number(item.get("score"))
    status = competition.get("status") or header.get("status") or {}
    status_type = status.get("type") or {}
    clock = status.get("displayClock") or status.get("clock")
    period = status.get("period")
    return {
        "player_id": player_id, "event_id": event_id, "team": team,
        "stats": stats, "score": score,
        "game": {
            "name": competition.get("name") or header.get("name"),
            "status": status_type.get("shortDetail") or status_type.get("detail") or status.get("display"),
            "state": status_type.get("state"),
            "period": period, "clock": clock,
        },
        "source": "ESPN", "updated_at": datetime.now(timezone.utc).isoformat(),
    }

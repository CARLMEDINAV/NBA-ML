"""Actualiza los defensores que mas marcaron a cada jugador de la web.

La NBA publica datos de matchup por partido. Este script los resume por
jugador/defensor y conserva solo los defensores con mas minutos de matchup,
para que la ficha del jugador pueda mostrar quien lo defendio sin consultar
``stats.nba.com`` desde Vercel.

Si la descarga falla, se mantiene el artefacto anterior. El workflow no se
rompe por un timeout aislado.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
ARTIFACT = ARTIFACTS / "player_defenders_recent.csv"
CURRENT_SEASON = "2026-27"
PRIOR_SEASON = "2025-26"
# Se muestran 5; guardamos 30 para poder encontrar un posible defensor del
# proximo rival aunque no este entre los primeros de la muestra historica.
TOP_DEFENDERS = 30

COLUMNS = [
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

RENAMES = {
    "OFF_PLAYER_ID": "off_player_id",
    "DEF_PLAYER_ID": "def_player_id",
    "DEF_PLAYER_NAME": "def_player_name",
    "GP": "gp",
    "MATCHUP_MIN": "matchup_min_text",
    "MATCHUP_TIME_SEC": "matchup_time_sec",
    "PLAYER_PTS": "player_pts",
    "MATCHUP_AST": "matchup_ast",
    "MATCHUP_TOV": "matchup_tov",
    "MATCHUP_BLK": "matchup_blk",
    "MATCHUP_FGM": "matchup_fgm",
    "MATCHUP_FGA": "matchup_fga",
    "MATCHUP_FG3M": "matchup_fg3m",
    "MATCHUP_FG3A": "matchup_fg3a",
    "MATCHUP_FTM": "matchup_ftm",
    "MATCHUP_FTA": "matchup_fta",
}

NUMERIC = [
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


def _team_map() -> dict[int, str]:
    """Equipo actual del defensor; si ya no esta en la liga, su ultimo equipo."""
    teams: dict[int, str] = {}
    current_path = ARTIFACTS / "player_state.csv"
    if current_path.exists():
        current = pd.read_csv(current_path, usecols=["player_id", "team"])
        teams.update(
            {
                int(row.player_id): str(row.team).upper().strip()
                for row in current.itertuples(index=False)
                if not pd.isna(row.team)
            }
        )
    prior_path = ARTIFACTS / f"player_stats_{PRIOR_SEASON.replace('-', '_')}_pergame.csv"
    if prior_path.exists():
        prior = pd.read_csv(prior_path, usecols=["player_id", "team"])
        for row in prior.itertuples(index=False):
            if pd.isna(row.team):
                continue
            teams.setdefault(int(row.player_id), str(row.team).upper().strip())
    return teams


def _matchup_minutes(frame: pd.DataFrame) -> pd.Series:
    if "matchup_time_sec" in frame.columns:
        seconds = pd.to_numeric(frame["matchup_time_sec"], errors="coerce")
    else:
        seconds = pd.Series(pd.NA, index=frame.index, dtype="Float64")
    minutes = pd.to_numeric(seconds, errors="coerce") / 60
    if "matchup_min_text" in frame.columns:
        parsed = (
            frame["matchup_min_text"]
            .astype(str)
            .str.strip()
            .str.split(":")
            .apply(
                lambda parts: (
                    float(parts[0]) + float(parts[1]) / 60
                    if len(parts) == 2
                    and parts[0].replace(".", "", 1).isdigit()
                    and parts[1].replace(".", "", 1).isdigit()
                    else None
                )
            )
        )
        minutes = minutes.fillna(parsed)
    return minutes


def _normalize(raw: pd.DataFrame, season: str, player_ids: set[int]) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=COLUMNS)
    frame = raw.rename(columns=RENAMES).copy()
    required = {"off_player_id", "def_player_id", "def_player_name"}
    if not required.issubset(frame.columns):
        return pd.DataFrame(columns=COLUMNS)

    frame["season"] = season
    frame["off_player_id"] = pd.to_numeric(
        frame["off_player_id"], errors="coerce"
    )
    frame["def_player_id"] = pd.to_numeric(
        frame["def_player_id"], errors="coerce"
    )
    frame = frame.dropna(subset=["off_player_id", "def_player_id"])
    frame["off_player_id"] = frame["off_player_id"].astype(int)
    frame["def_player_id"] = frame["def_player_id"].astype(int)
    frame = frame[frame["off_player_id"].isin(player_ids)].copy()
    if frame.empty:
        return pd.DataFrame(columns=COLUMNS)

    frame["matchup_min"] = _matchup_minutes(frame)
    for column in NUMERIC:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)

    teams = _team_map()
    frame["def_team"] = frame["def_player_id"].map(teams)
    frame["def_player_name"] = frame["def_player_name"].astype(str)

    frame = frame.sort_values(
        ["season", "off_player_id", "matchup_min"],
        ascending=[True, True, False],
    )
    frame = frame.groupby(
        ["season", "off_player_id"], sort=False, group_keys=False
    ).head(TOP_DEFENDERS)
    return frame[COLUMNS].reset_index(drop=True)


def _fetch(season: str, player_ids: set[int]) -> pd.DataFrame:
    from nba_api.stats.endpoints import leagueseasonmatchups

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            endpoint = leagueseasonmatchups.LeagueSeasonMatchups(
                season=season,
                season_type_playoffs="Regular Season",
                per_mode_simple="Totals",
                timeout=120,
            )
            return _normalize(endpoint.get_data_frames()[0], season, player_ids)
        except Exception as error:  # noqa: BLE001 - reintentar timeouts de la NBA
            last_error = error
            if attempt < 2:
                time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"No se pudieron descargar los matchups {season}: {last_error}")


def _current_player_ids() -> set[int]:
    path = ARTIFACTS / "player_state.csv"
    if not path.exists():
        raise FileNotFoundError("Falta artifacts/player_state.csv")
    state = pd.read_csv(path, usecols=["player_id"])
    return set(state["player_id"].astype(int))


def main() -> int:
    backfill = "--backfill" in sys.argv
    seasons = [PRIOR_SEASON, CURRENT_SEASON] if backfill else [CURRENT_SEASON]
    try:
        player_ids = _current_player_ids()
    except Exception as error:  # noqa: BLE001
        print(f"::warning::No se pueden actualizar defensores: {error}")
        return 1

    frames: list[pd.DataFrame] = []
    try:
        for season in seasons:
            frame = _fetch(season, player_ids)
            frames.append(frame)
            print(f"matchups {season}: {len(frame)} filas")
    except Exception as error:  # noqa: BLE001
        print(f"::warning::No se pudieron actualizar los defensores: {error}")
        if ARTIFACT.exists():
            print("Se conserva el artefacto de defensores anterior.")
            return 0
        return 1

    if ARTIFACT.exists():
        previous = pd.read_csv(ARTIFACT, dtype={"season": str})
        previous = previous[
            ~previous["season"].astype(str).isin(seasons)
        ].copy()
    else:
        previous = pd.DataFrame(columns=COLUMNS)

    parts = [previous, *frames] if not previous.empty else frames
    combined = pd.concat(parts, ignore_index=True, sort=False)
    for column in COLUMNS:
        if column not in combined.columns:
            combined[column] = None
    combined = combined[COLUMNS]
    combined = combined.drop_duplicates(
        ["season", "off_player_id", "def_player_id"], keep="last"
    )
    combined = combined.sort_values(
        ["season", "off_player_id", "matchup_min"],
        ascending=[True, True, False],
    )
    combined = combined.groupby(
        ["season", "off_player_id"], sort=False, group_keys=False
    ).head(TOP_DEFENDERS)

    temporary = ARTIFACT.with_suffix(".tmp")
    combined.to_csv(temporary, index=False)
    temporary.replace(ARTIFACT)
    print(
        f"{ARTIFACT.relative_to(ROOT)}: {len(combined)} filas para "
        f"{combined['off_player_id'].nunique()} jugadores"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

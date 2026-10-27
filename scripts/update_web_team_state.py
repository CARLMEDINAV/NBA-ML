"""Actualiza el estado de los equipos con los resultados de la temporada actual.

El modelo necesita el ultimo partido, los promedios moviles, el Elo y la
densidad de calendario de cada equipo. ``team_state.csv`` se generaba solo al
entrenar, asi que al empezar 2026-27 seguia siendo una foto de 2025-26 y la
prediccion asumia descanso maximo.

Este script mantiene un historial compacto (``team_games_recent.csv``) con los
ultimos partidos de cada equipo y lo completa con los boxscores de ESPN. Con eso
recalcula el estado sin necesitar los CSV completos de ``data/`` en GitHub
Actions.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nba_ml.config import (  # noqa: E402
    ELO_BASE,
    ELO_HOME_ADVANTAGE,
    ELO_K,
    ELO_SEASON_CARRYOVER,
)
from nba_ml.data.build_dataset import LEAGUE_WINDOWS, build_team_state  # noqa: E402
from nba_ml.data.espn import append_recent_team_games  # noqa: E402

SEASON = "2026-27"
EASTERN = ZoneInfo("America/New_York")
SCHEDULE = ROOT / "artifacts" / "schedule_2026_27.csv"
HISTORY = ROOT / "artifacts" / "team_games_recent.csv"
STATE = ROOT / "artifacts" / "team_state.csv"
PROCESSED_STATE = ROOT / "data" / "processed" / "team_state.csv"


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, dtype={"game_id": str})
    if "game_date" in frame.columns:
        frame["game_date"] = pd.to_datetime(frame["game_date"], errors="coerce")
    return frame


def _finals(schedule: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "game_id",
        "game_date",
        "home",
        "away",
        "home_score",
        "away_score",
        "total_points",
        "status_text",
    ]
    if schedule.empty:
        return pd.DataFrame(columns=columns)
    frame = schedule[pd.to_numeric(schedule["status"], errors="coerce").eq(3)].copy()
    frame["game_date"] = pd.to_datetime(frame["game_date"], errors="coerce")
    frame["home_score"] = pd.to_numeric(frame["home_score"], errors="coerce")
    frame["away_score"] = pd.to_numeric(frame["away_score"], errors="coerce")
    frame = frame.dropna(subset=["game_date", "home", "away", "home_score", "away_score"])
    return frame.sort_values(["game_date", "game_id"]).reset_index(drop=True)


def _trim_history(history: pd.DataFrame) -> pd.DataFrame:
    """Conserva toda la temporada actual y los ultimos 10 de cada equipo antes."""
    if history.empty:
        return history
    history = history.copy()
    history["game_date"] = pd.to_datetime(history["game_date"], errors="coerce")
    current = history[history["season"].astype(str).eq(SEASON)]
    previous = (
        history[~history["season"].astype(str).eq(SEASON)]
        .sort_values(["team_id", "game_date", "game_id"])
        .groupby("team_id", sort=False)
        .tail(10)
    )
    return (
        pd.concat([previous, current], ignore_index=True)
        .sort_values(["team_id", "game_date", "game_id"], kind="stable")
        .reset_index(drop=True)
    )


def _schedule_dates(schedule: pd.DataFrame) -> dict[str, list[pd.Timestamp]]:
    finals = _finals(schedule)
    dates: dict[str, list[pd.Timestamp]] = {}
    for row in finals.itertuples(index=False):
        for team in (str(row.home), str(row.away)):
            dates.setdefault(team, []).append(pd.Timestamp(row.game_date))
    return dates


def _apply_schedule_dates(
    state: pd.DataFrame, schedule: pd.DataFrame, today: pd.Timestamp
) -> pd.DataFrame:
    """Garantiza que el descanso use el calendario actual aunque falle ESPN."""
    dates_by_team = _schedule_dates(schedule)
    for index in state.index:
        dates = dates_by_team.get(str(state.at[index, "team_abbreviation"]))
        if not dates:
            continue
        dates = sorted(set(dates))
        last = dates[-1]
        state.at[index, "last_game_date"] = last
        state.at[index, "season"] = SEASON
        state.at[index, "season_game_no"] = len(dates) + 1
        state.at[index, "recent_game_dates"] = ",".join(
            date.strftime("%Y-%m-%d") for date in dates[-10:]
        )
        state.at[index, "days_rest"] = float(
            np.clip((today - last).days, 0, 10)
        )
        state.at[index, "is_b2b"] = int(state.at[index, "days_rest"] <= 1)
        state.at[index, "games_last_7d"] = int(
            sum(today - pd.Timedelta(days=7) <= date < today for date in dates)
        )
        state.at[index, "games_last_14d"] = int(
            sum(today - pd.Timedelta(days=14) <= date < today for date in dates)
        )
    return state


def _elo_update(
    previous: pd.DataFrame, schedule: pd.DataFrame
) -> tuple[pd.Series, pd.Series]:
    """Recalcula Elo de la temporada desde la foto de inicio de temporada."""
    ratings: dict[str, float] = {}
    starts: dict[str, float] = {}
    has_start = "elo_season_start" in previous.columns
    for row in previous.itertuples(index=False):
        team = str(row.team_abbreviation)
        old = float(row.elo) if pd.notna(row.elo) else ELO_BASE
        if (
            has_start
            and str(getattr(row, "season", "")) == SEASON
            and pd.notna(getattr(row, "elo_season_start", np.nan))
        ):
            start = float(row.elo_season_start)
        else:
            start = ELO_BASE + ELO_SEASON_CARRYOVER * (old - ELO_BASE)
        starts[team] = start
        ratings[team] = start

    for row in _finals(schedule).itertuples(index=False):
        home = str(row.home)
        away = str(row.away)
        ratings.setdefault(home, ELO_BASE)
        ratings.setdefault(away, ELO_BASE)
        starts.setdefault(home, ELO_BASE)
        starts.setdefault(away, ELO_BASE)
        home_elo = ratings[home]
        away_elo = ratings[away]
        expected_home = 1.0 / (
            1.0 + 10.0 ** (-((home_elo + ELO_HOME_ADVANTAGE) - away_elo) / 400.0)
        )
        margin = float(row.home_score) - float(row.away_score)
        actual_home = 1.0 if margin > 0 else 0.0
        multiplier = ((abs(margin) + 3.0) ** 0.8) / 7.5
        delta = ELO_K * multiplier * (actual_home - expected_home)
        ratings[home] = home_elo + delta
        ratings[away] = away_elo - delta

    return pd.Series(ratings), pd.Series(starts)


def _league_update(
    previous: pd.DataFrame, schedule: pd.DataFrame
) -> tuple[dict[str, float], dict[str, float]]:
    """Actualiza el contexto de anotacion con una mezcla de foto y partidos."""
    finals = _finals(schedule)
    totals = finals["total_points"].dropna().astype(float).tolist()
    same_season = (
        "season" in previous.columns
        and previous["season"].astype(str).eq(SEASON).all()
    )
    values: dict[str, float] = {}
    starts: dict[str, float] = {}
    for window in LEAGUE_WINDOWS:
        column = f"league_total_roll{window}"
        start_column = f"{column}_season_start"
        old = float(previous[column].iloc[0]) if column in previous.columns else np.nan
        if (
            same_season
            and start_column in previous.columns
            and pd.notna(previous[start_column].iloc[0])
        ):
            base = float(previous[start_column].iloc[0])
        else:
            base = old
        starts[column] = base
        if totals and pd.notna(base):
            recent = totals[-window:]
            missing = max(0, window - len(recent))
            values[column] = (base * missing + sum(recent)) / window
        elif totals:
            values[column] = float(np.mean(totals[-window:]))
        else:
            values[column] = base
    return values, starts


def main() -> int:
    schedule = _read(SCHEDULE)
    previous_state = _read(STATE)
    history = _read(HISTORY)
    if history.empty:
        print(f"::warning::No existe {HISTORY.name}; no se puede actualizar el estado.")
        return 0 if previous_state.exists() else 1

    today = datetime.now(EASTERN).date()
    team_names = (
        dict(
            zip(
                previous_state["team_abbreviation"].astype(str),
                previous_state["team_name"].astype(str),
            )
        )
        if not previous_state.empty
        else {}
    )
    try:
        history = append_recent_team_games(
            history,
            schedule,
            SEASON,
            today=today,
            days_back=30,
            team_names=team_names,
        )
    except Exception as error:  # noqa: BLE001 - no romper el workflow por red
        print(f"::warning::No se pudieron agregar boxscores de equipos: {error}")

    # Elimina pretemporada u otros partidos que no esten en el calendario
    # regular publicado.
    valid_ids = set(_finals(schedule)["game_id"].astype(str))
    if valid_ids:
        history = history[
            ~history["season"].astype(str).eq(SEASON)
            | history["game_id"].astype(str).isin(valid_ids)
        ].copy()

    history = _trim_history(history)
    if history.empty:
        print("::warning::El historial de equipos esta vacio.")
        return 0 if not previous_state.empty else 1

    state = build_team_state(history, as_of=pd.Timestamp(today))
    state = _apply_schedule_dates(state, schedule, pd.Timestamp(today))

    if previous_state.empty:
        state["elo"] = ELO_BASE
        state["elo_season_start"] = ELO_BASE
        for window in LEAGUE_WINDOWS:
            state[f"league_total_roll{window}"] = np.nan
            state[f"league_total_roll{window}_season_start"] = np.nan
    else:
        elo, elo_start = _elo_update(previous_state, schedule)
        state["elo"] = state["team_abbreviation"].map(elo)
        state["elo_season_start"] = state["team_abbreviation"].map(elo_start)
        league, league_start = _league_update(previous_state, schedule)
        for column, value in league.items():
            state[column] = value
            state[f"{column}_season_start"] = league_start[column]
        # Mantiene el orden historico y anade las columnas nuevas al final.
        columns = list(previous_state.columns)
        for column in state.columns:
            if column not in columns:
                columns.append(column)
        state = state.reindex(columns=columns)

    state = state.sort_values("team_id").reset_index(drop=True)
    PROCESSED_STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state.to_csv(PROCESSED_STATE, index=False)
    shutil.copy2(PROCESSED_STATE, STATE)
    history["game_date"] = pd.to_datetime(history["game_date"]).dt.strftime("%Y-%m-%d")
    history.to_csv(HISTORY, index=False)

    last_dates = pd.to_datetime(state["last_game_date"])
    print(
        f"{STATE.relative_to(ROOT)}: {len(state)} equipos, "
        f"ultimo partido {last_dates.min().date()} - {last_dates.max().date()}"
    )
    print(f"{HISTORY.relative_to(ROOT)}: {len(history)} filas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

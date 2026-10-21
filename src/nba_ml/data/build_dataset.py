"""Construccion del dataset de partidos y de las features pre-partido.

Regla de oro: toda feature de un partido se calcula *solo* con informacion
anterior a ese partido. Los promedios moviles se desplazan un partido
(``shift(1)``) y Elo se guarda antes de actualizarse con el resultado.

Salidas (``data/processed/``):
    - ``games.csv``: una fila por partido, features + target ``total_points``.
    - ``team_games.csv``: una fila por equipo-partido (auditoria y EDA).
    - ``team_state.csv``: ultimo estado conocido de cada equipo (prediccion).
"""

from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np
import pandas as pd

from ..config import (
    ELO_BASE,
    ELO_HOME_ADVANTAGE,
    ELO_K,
    ELO_SEASON_CARRYOVER,
    LONG_WINDOW,
    META_COLUMNS,
    PROCESSED_DIR,
    SHORT_WINDOW,
    TARGET,
    ensure_directories,
)
from .download import load_raw

LOG = logging.getLogger(__name__)

# Estadisticas por equipo-partido que alimentan los promedios moviles.
SHORT_STATS: tuple[str, ...] = ("pts", "opp_pts", "pace", "off_rtg", "def_rtg", "game_total")
LONG_STATS: tuple[str, ...] = SHORT_STATS + (
    "fg3a_rate",
    "fta_rate",
    "tov_rate",
    "ts",
    "win",
    "ot_flag",
    "close_flag",
)
EXPANDING_STATS: tuple[str, ...] = ("pts", "opp_pts", "pace", "off_rtg", "def_rtg")
LEAGUE_WINDOWS: tuple[int, ...] = (20, 50, 100, 300)

SCHEDULE_FEATURES: tuple[str, ...] = (
    "days_rest",
    "is_b2b",
    "games_last_7d",
    "games_last_14d",
    "season_game_no",
)

# Features por equipo (se duplican con sufijo _home / _away).
BASE_FEATURES: tuple[str, ...] = tuple(
    [f"{stat}_roll{SHORT_WINDOW}" for stat in SHORT_STATS]
    + [f"{stat}_roll{LONG_WINDOW}" for stat in LONG_STATS]
    + [f"{stat}_exp" for stat in EXPANDING_STATS]
    + list(SCHEDULE_FEATURES)
)

# Features derivadas del cruce de ambos equipos.
DERIVED_FEATURES: tuple[str, ...] = (
    "elo_home",
    "elo_away",
    "elo_diff",
    "rest_diff",
    "b2b_sum",
    "pace_sum",
    "off_rtg_sum",
    "def_rtg_sum",
    "exp_total",
    "form_total",
    "win_rate_diff",
    "ot_rate_sum",
    "close_rate_sum",
    "league_total_roll20",
    "league_total_roll50",
    "league_total_roll100",
    "league_total_roll300",
    "month",
)

MODEL_FEATURES: tuple[str, ...] = tuple(
    [f"{col}_home" for col in BASE_FEATURES]
    + [f"{col}_away" for col in BASE_FEATURES]
    + list(DERIVED_FEATURES)
)


# ---------------------------------------------------------------------------
# Normalizacion y emparejado
# ---------------------------------------------------------------------------
def normalize_raw(raw: pd.DataFrame) -> pd.DataFrame:
    """Pasa columnas a minusculas y garantiza tipos estables."""
    frame = raw.copy()
    frame.columns = [column.lower() for column in frame.columns]
    frame["game_date"] = pd.to_datetime(frame["game_date"])
    frame["game_id"] = frame["game_id"].astype(str).str.zfill(10)
    frame["team_id"] = frame["team_id"].astype(int)
    frame["season"] = frame["season"].astype(str)
    frame["is_home"] = frame["matchup"].str.contains("vs.", regex=False)

    unknown = frame[~frame["matchup"].str.contains(r"@|vs\.", regex=True)]
    if not unknown.empty:
        raise ValueError(
            "Partidos con formato de MATCHUP desconocido: "
            f"{unknown[['game_id', 'matchup']].head().to_dict('records')}"
        )
    return frame


def _valid_home_games(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Separa partidos con local/visitante identificable de los de sede neutral.

    En los NBA Mexico/Paris Games la API marca a los dos equipos como
    visitantes (``EQUIPO @ EQUIPO``), por lo que no hay forma fiable de asignar
    la ventaja de cancha. Se excluyen del modelado.
    """
    home_counts = frame[frame["is_home"]].groupby("game_id").size()
    valid = set(home_counts[home_counts == 1].index)
    neutral = sorted(set(frame["game_id"]) - valid)
    return frame[frame["game_id"].isin(valid)].copy(), neutral


def pair_games(raw: pd.DataFrame) -> pd.DataFrame:
    """Convierte los dos registros por partido en una sola fila local/visitante."""
    frame, _ = _valid_home_games(normalize_raw(raw))

    per_game = frame.groupby("game_id")["team_id"].nunique()
    invalid = per_game[per_game != 2]
    if not invalid.empty:
        raise ValueError(
            f"{len(invalid)} partidos no tienen exactamente dos equipos: "
            f"{invalid.head().to_dict()}"
        )

    home = frame[frame["is_home"]].set_index("game_id")
    away = frame[~frame["is_home"]].set_index("game_id")

    if home.index.has_duplicates or away.index.has_duplicates:
        raise ValueError("Hay mas de un equipo local o visitante por partido")

    games = home.join(away, lsuffix="_home", rsuffix="_away", how="inner")

    if (games["game_date_home"] != games["game_date_away"]).any():
        raise ValueError("Fechas inconsistentes entre local y visitante")

    games = pd.DataFrame(
        {
            "game_id": games.index,
            "game_date": games["game_date_home"],
            "season": games["season_home"],
            "home_team_id": games["team_id_home"],
            "away_team_id": games["team_id_away"],
            "home_team": games["team_abbreviation_home"],
            "away_team": games["team_abbreviation_away"],
            "home_pts": games["pts_home"].astype(int),
            "away_pts": games["pts_away"].astype(int),
        }
    )
    games[TARGET] = games["home_pts"] + games["away_pts"]
    games["home_margin"] = games["home_pts"] - games["away_pts"]
    games["home_win"] = (games["home_margin"] > 0).astype(int)
    games = games.reset_index(drop=True)
    games = games.sort_values(["game_date", "game_id"]).reset_index(drop=True)

    if games["game_id"].duplicated().any():
        raise ValueError("game_id duplicado tras emparejar")
    return games


def build_team_games(raw: pd.DataFrame) -> pd.DataFrame:
    """Tabla larga equipo-partido con estadisticas propias y del rival."""
    frame, _ = _valid_home_games(normalize_raw(raw))
    opponent = frame[
        ["game_id", "team_id", "pts", "fga", "fta", "oreb", "tov", "dreb"]
    ].rename(
        columns={
            "team_id": "opponent_id",
            "pts": "opp_pts",
            "fga": "fga_opp",
            "fta": "fta_opp",
            "oreb": "oreb_opp",
            "tov": "tov_opp",
            "dreb": "dreb_opp",
        }
    )
    merged = frame.merge(opponent, on="game_id", how="inner")
    merged = merged[merged["team_id"] != merged["opponent_id"]].copy()

    merged["pace"] = merged["fga"] + 0.44 * merged["fta"] - merged["oreb"] + merged["tov"]
    pace_opp = merged["fga_opp"] + 0.44 * merged["fta_opp"] - merged["oreb_opp"] + merged["tov_opp"]

    merged["off_rtg"] = 100.0 * merged["pts"] / merged["pace"]
    merged["def_rtg"] = 100.0 * merged["opp_pts"] / pace_opp
    merged["fg3a_rate"] = merged["fg3a"] / merged["fga"]
    merged["fta_rate"] = merged["fta"] / merged["fga"]
    merged["tov_rate"] = merged["tov"] / merged["pace"]
    merged["ts"] = merged["pts"] / (2.0 * (merged["fga"] + 0.44 * merged["fta"]))
    merged["oreb_rate"] = merged["oreb"] / (merged["oreb"] + merged["dreb_opp"])
    merged["game_total"] = merged["pts"] + merged["opp_pts"]
    merged["win"] = (merged["wl"] == "W").astype(int)
    merged["ot_flag"] = (merged["min"] > 240).astype(float)
    merged["close_flag"] = (
        (merged["pts"] - merged["opp_pts"]).abs() <= 5
    ).astype(float)

    numeric = ["pace", "off_rtg", "def_rtg", "fg3a_rate", "fta_rate", "tov_rate", "ts", "oreb_rate"]
    merged[numeric] = merged[numeric].replace([np.inf, -np.inf], np.nan)

    return merged.sort_values(["team_id", "game_date", "game_id"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------
def _count_prior_games(dates: pd.Series, days: int) -> pd.Series:
    """Numero de partidos del equipo en los ``days`` dias previos (exclusivo)."""
    values = dates.to_numpy(dtype="datetime64[ns]")
    lower_bounds = values - np.timedelta64(days, "D")
    starts = np.searchsorted(values, lower_bounds, side="left")
    counts = np.arange(len(values)) - starts
    return pd.Series(counts, index=dates.index, dtype="int64")


def add_team_features(team_games: pd.DataFrame) -> pd.DataFrame:
    """Anade promedios moviles, forma de la temporada y descanso (pre-partido)."""
    frame = team_games.sort_values(["team_id", "game_date", "game_id"]).reset_index(drop=True)

    chunks: list[pd.DataFrame] = []
    for _, group in frame.groupby("team_id", sort=False):
        group = group.copy()

        for window, stats in ((SHORT_WINDOW, SHORT_STATS), (LONG_WINDOW, LONG_STATS)):
            rolled = group[list(stats)].shift(1).rolling(window, min_periods=3).mean()
            rolled.columns = [f"{stat}_roll{window}" for stat in stats]
            group = pd.concat([group, rolled], axis=1)

        expanding = (
            group.groupby("season")[list(EXPANDING_STATS)]
            .transform(lambda series: series.shift(1).expanding(min_periods=3).mean())
        )
        expanding.columns = [f"{stat}_exp" for stat in EXPANDING_STATS]
        group = pd.concat([group, expanding], axis=1)

        previous_date = group["game_date"].shift(1)
        rest = (group["game_date"] - previous_date).dt.days.clip(lower=0, upper=10)
        group["days_rest"] = rest
        group["is_b2b"] = (rest <= 1).fillna(False).astype(int)
        group["games_last_7d"] = _count_prior_games(group["game_date"], 7)
        group["games_last_14d"] = _count_prior_games(group["game_date"], 14)
        group["season_game_no"] = group.groupby("season").cumcount() + 1

        chunks.append(group)

    return pd.concat(chunks, ignore_index=True).sort_values(
        ["game_date", "game_id", "team_id"]
    ).reset_index(drop=True)


def add_elo(
    games: pd.DataFrame,
    k: float = ELO_K,
    home_advantage: float = ELO_HOME_ADVANTAGE,
    season_carryover: float = ELO_SEASON_CARRYOVER,
    base_rating: float = ELO_BASE,
) -> pd.DataFrame:
    """Elo pre-partido con ajuste por margen y regresion entre temporadas."""
    ratings: dict[int, float] = defaultdict(lambda: base_rating)
    home_ratings: list[float] = []
    away_ratings: list[float] = []
    post_home: list[float] = []
    post_away: list[float] = []
    current_season: str | None = None

    for row in games.sort_values(["game_date", "game_id"]).itertuples(index=False):
        if row.season != current_season:
            if current_season is not None:
                for team_id in list(ratings):
                    ratings[team_id] = base_rating + season_carryover * (
                        ratings[team_id] - base_rating
                    )
            current_season = row.season

        home_elo = ratings[row.home_team_id]
        away_elo = ratings[row.away_team_id]
        home_ratings.append(home_elo)
        away_ratings.append(away_elo)

        expected_home = 1.0 / (
            1.0 + 10.0 ** (-((home_elo + home_advantage) - away_elo) / 400.0)
        )
        actual_home = 1.0 if row.home_margin > 0 else 0.0
        margin_multiplier = ((abs(row.home_margin) + 3.0) ** 0.8) / 7.5
        delta = k * margin_multiplier * (actual_home - expected_home)

        ratings[row.home_team_id] = home_elo + delta
        ratings[row.away_team_id] = away_elo - delta
        post_home.append(home_elo + delta)
        post_away.append(away_elo - delta)

    result = games.copy()
    result["elo_home"] = home_ratings
    result["elo_away"] = away_ratings
    result["elo_post_home"] = post_home
    result["elo_post_away"] = post_away
    return result


def add_league_context(games: pd.DataFrame) -> pd.DataFrame:
    """Medias moviles causales del total de la liga (tendencia de epoca)."""
    result = games.sort_values(["game_date", "game_id"]).copy()
    for window in LEAGUE_WINDOWS:
        result[f"league_total_roll{window}"] = (
            result[TARGET].shift(1).rolling(window, min_periods=20).mean()
        )
    return result


def add_derived_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Features de interaccion local/visitante. Reutilizada en prediccion."""
    result = frame.copy()
    result["elo_diff"] = result["elo_home"] - result["elo_away"]
    result["rest_diff"] = result["days_rest_home"] - result["days_rest_away"]
    result["b2b_sum"] = result["is_b2b_home"] + result["is_b2b_away"]
    result["pace_sum"] = result["pace_roll10_home"] + result["pace_roll10_away"]
    result["off_rtg_sum"] = result["off_rtg_roll10_home"] + result["off_rtg_roll10_away"]
    result["def_rtg_sum"] = result["def_rtg_roll10_home"] + result["def_rtg_roll10_away"]
    result["exp_total"] = (
        result["pace_sum"] * (result["off_rtg_sum"] + result["def_rtg_sum"]) / 200.0
    )
    result["form_total"] = result[
        ["game_total_roll10_home", "game_total_roll10_away"]
    ].mean(axis=1)
    result["win_rate_diff"] = result["win_roll10_home"] - result["win_roll10_away"]
    result["ot_rate_sum"] = result["ot_flag_roll10_home"] + result["ot_flag_roll10_away"]
    result["close_rate_sum"] = (
        result["close_flag_roll10_home"] + result["close_flag_roll10_away"]
    )
    return result


def build_game_features(games: pd.DataFrame, team_games: pd.DataFrame) -> pd.DataFrame:
    """Cruce de features de ambos equipos + features derivadas."""
    featured = add_team_features(team_games)

    home = featured[featured["is_home"]].set_index("game_id")[list(BASE_FEATURES)]
    away = featured[~featured["is_home"]].set_index("game_id")[list(BASE_FEATURES)]

    dataset = (
        add_league_context(add_elo(games))
        .set_index("game_id")
        .join(home.add_suffix("_home"), how="inner")
        .join(away.add_suffix("_away"), how="inner")
    )
    overtime = featured[featured["is_home"]].set_index("game_id")["ot_flag"]
    dataset["is_overtime"] = dataset.index.map(overtime).astype(int)
    dataset = dataset.reset_index()
    dataset["month"] = dataset["game_date"].dt.month

    dataset = add_derived_features(dataset)
    dataset = dataset.sort_values(["game_date", "game_id"]).reset_index(drop=True)

    missing = [column for column in MODEL_FEATURES if column not in dataset.columns]
    if missing:
        raise ValueError(f"Faltan features en el dataset: {missing}")

    return dataset


# ---------------------------------------------------------------------------
# Estado actual por equipo (para predecir partidos futuros)
# ---------------------------------------------------------------------------
def build_team_state(team_games: pd.DataFrame, as_of: str | pd.Timestamp | None = None) -> pd.DataFrame:
    """Resume el estado de cada equipo para su *proximo* partido.

    A diferencia de las features historicas, aqui los promedios si incluyen el
    ultimo partido jugado: es la informacion disponible antes del siguiente.
    """
    featured = add_team_features(team_games)
    last_rows = featured.sort_values(["team_id", "game_date", "game_id"]).groupby("team_id").tail(1)
    reference = pd.Timestamp(as_of) if as_of is not None else None

    states: list[dict[str, object]] = []
    for _, last in last_rows.iterrows():
        history = featured[featured["team_id"] == last["team_id"]].sort_values(
            ["game_date", "game_id"]
        )
        state: dict[str, object] = {
            "team_id": int(last["team_id"]),
            "team_abbreviation": last["team_abbreviation"],
            "team_name": last["team_name"],
            "season": last["season"],
            "last_game_date": last["game_date"],
        }

        for window, stats in ((SHORT_WINDOW, SHORT_STATS), (LONG_WINDOW, LONG_STATS)):
            tail = history.tail(window)
            for stat in stats:
                state[f"{stat}_roll{window}"] = (
                    tail[stat].mean() if len(tail) >= 3 else np.nan
                )

        current_season = history[history["season"] == last["season"]]
        for stat in EXPANDING_STATS:
            state[f"{stat}_exp"] = (
                current_season[stat].mean() if len(current_season) >= 3 else np.nan
            )

        last_date = pd.Timestamp(last["game_date"])
        if reference is not None:
            rest = int((reference - last_date).days)
            state["days_rest"] = float(np.clip(rest, 0, 10))
            window_start = reference - pd.Timedelta(days=7)
            window_start_14 = reference - pd.Timedelta(days=14)
        else:
            state["days_rest"] = 3.0
            window_start = last_date + pd.Timedelta(days=3) - pd.Timedelta(days=7)
            window_start_14 = last_date + pd.Timedelta(days=3) - pd.Timedelta(days=14)

        dates = history["game_date"]
        if reference is not None:
            in_last_7 = ((dates >= window_start) & (dates < reference)).sum()
            in_last_14 = ((dates >= window_start_14) & (dates < reference)).sum()
        else:
            in_last_7 = ((dates > window_start) & (dates <= last_date)).sum()
            in_last_14 = ((dates > window_start_14) & (dates <= last_date)).sum()

        state["is_b2b"] = int(state["days_rest"] <= 1)
        state["games_last_7d"] = int(in_last_7)
        state["games_last_14d"] = int(in_last_14)
        state["season_game_no"] = int(last["season_game_no"]) + 1
        state["recent_game_dates"] = ",".join(
            history["game_date"].dt.strftime("%Y-%m-%d").tail(10).tolist()
        )

        states.append(state)

    state_frame = pd.DataFrame(states)
    state_frame["month"] = (
        reference.month if reference is not None else pd.Timestamp.today().month
    )
    return state_frame


def build_feature_row(
    home_state: pd.Series,
    away_state: pd.Series,
) -> pd.DataFrame:
    """Construye la fila de features de un enfrentamiento futuro."""
    row: dict[str, float] = {}
    for column in BASE_FEATURES:
        row[f"{column}_home"] = home_state[column]
        row[f"{column}_away"] = away_state[column]

    row["elo_home"] = home_state["elo"]
    row["elo_away"] = away_state["elo"]
    for window in LEAGUE_WINDOWS:
        row[f"league_total_roll{window}"] = home_state[f"league_total_roll{window}"]
    row["month"] = home_state["month"]

    frame = add_derived_features(pd.DataFrame([row]))
    return frame[list(MODEL_FEATURES)]


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------
def latest_elo(dataset: pd.DataFrame) -> pd.Series:
    """Elo mas reciente de cada equipo a partir del dataset de partidos."""
    home = dataset[
        ["game_date", "game_id", "home_team_id", "elo_post_home"]
    ].rename(columns={"home_team_id": "team_id", "elo_post_home": "elo"})
    away = dataset[
        ["game_date", "game_id", "away_team_id", "elo_post_away"]
    ].rename(columns={"away_team_id": "team_id", "elo_post_away": "elo"})
    combined = pd.concat([home, away], ignore_index=True).sort_values(
        ["game_date", "game_id"]
    )
    return combined.groupby("team_id")["elo"].last()


def build_dataset(raw: pd.DataFrame | None = None) -> pd.DataFrame:
    """Genera ``games.csv``, ``team_games.csv`` y ``team_state.csv``."""
    ensure_directories()
    if raw is None:
        raw = load_raw()

    _, neutral = _valid_home_games(normalize_raw(raw))
    if neutral:
        LOG.warning(
            "Excluidos %s partidos de sede neutral sin local/visitante: %s",
            len(neutral),
            ", ".join(neutral),
        )

    games = pair_games(raw)
    team_games = build_team_games(raw)
    dataset = build_game_features(games, team_games)

    dataset.to_csv(PROCESSED_DIR / "games.csv", index=False, encoding="utf-8")
    team_games.to_csv(PROCESSED_DIR / "team_games.csv", index=False, encoding="utf-8")

    state = build_team_state(team_games)
    state["elo"] = state["team_id"].map(latest_elo(dataset))
    for window in LEAGUE_WINDOWS:
        column = f"league_total_roll{window}"
        state[column] = float(dataset[column].dropna().iloc[-1])
    state.to_csv(PROCESSED_DIR / "team_state.csv", index=False, encoding="utf-8")

    LOG.info(
        "Dataset: %s partidos, %s features, %s-%s",
        len(dataset),
        len(MODEL_FEATURES),
        dataset["season"].min(),
        dataset["season"].max(),
    )
    return dataset


def load_dataset() -> pd.DataFrame:
    """Carga el dataset procesado y anade el contexto de liga mas reciente."""
    path = PROCESSED_DIR / "games.csv"
    if not path.exists():
        raise FileNotFoundError(f"No existe {path}. Ejecuta primero: nba-ml build")

    dataset = pd.read_csv(path, dtype={"game_id": str})
    dataset["game_date"] = pd.to_datetime(dataset["game_date"])

    unexpected = set(META_COLUMNS) & set(MODEL_FEATURES)
    if unexpected:
        raise ValueError(f"Columnas meta usadas como feature: {unexpected}")
    return dataset

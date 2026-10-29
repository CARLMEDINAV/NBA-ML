"""Calibra el ajuste historico de disponibilidad usado por la API.

El ajuste responde a una pregunta concreta: cuanto cambia el margen de un
equipo cuando falta parte de su top-5 de PRA. No es un modelo de minutos ni
una probabilidad oficial.

Metodo:
1. Para cada temporada, se define el top-5 de cada equipo con el PRA medio de
   la temporada anterior (sin usar el resultado del partido actual).
2. En cada equipo-partido se calcula la fraccion de PRA del top-5 que no jugo.
3. Se ajusta una regresion del margen con efectos fijos de equipo, rival y
   temporada sobre 2024-25.
4. Se evalua el coeficiente en 2025-26, que no se usa para ajustar.

Uso:
    .\\.venv\\Scripts\\python.exe scripts\\calibrate_availability_impact.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score

ROOT = Path(__file__).resolve().parents[1]
TEAM_GAMES = ROOT / "data" / "processed" / "team_games.csv"
SEASONS = ("2023-24", "2024-25", "2025-26")


def _logs(season: str) -> pd.DataFrame:
    path = ROOT / "data" / "raw" / f"player_game_logs_{season.replace('-', '_')}.csv"
    return pd.read_csv(path, parse_dates=["game_date"])


def _availability_table(
    team_games: pd.DataFrame,
    logs: dict[str, pd.DataFrame],
    season: str,
    prior_season: str,
) -> pd.DataFrame:
    games = team_games[team_games.season == season].copy()
    current = logs[season]
    prior_pra = logs[prior_season].groupby("player_id").pra.mean()

    roster = (
        current.groupby(["player_id", "team"])
        .agg(
            games=("game_id", "nunique"),
            first=("game_date", "min"),
            last=("game_date", "max"),
        )
        .reset_index()
    )
    roster = roster[roster.games >= 20].copy()
    roster["prior_pra"] = roster.player_id.map(prior_pra)
    roster = roster.dropna(subset=["prior_pra"])
    team_keys = {
        team: group.sort_values("prior_pra", ascending=False).head(5)
        for team, group in roster.groupby("team")
    }

    played = (
        current[current["min"] > 0]
        .groupby(["game_id", "team"])
        .player_id.apply(set)
        .to_dict()
    )
    id_to_abbr = (
        games.drop_duplicates("team_id").set_index("team_id").team_abbreviation.to_dict()
    )

    availability: dict[tuple[int, str], float] = {}
    for row in games.itertuples(index=False):
        keys = team_keys.get(row.team_abbreviation)
        if keys is None:
            continue
        active = keys[
            (keys["first"] <= row.game_date) & (keys["last"] >= row.game_date)
        ]
        if active.empty:
            continue
        denominator = float(active.prior_pra.sum())
        if denominator <= 0:
            continue
        played_ids = played.get((row.game_id, row.team_abbreviation), set())
        missing = active[~active.player_id.isin(played_ids)]
        availability[(row.game_id, row.team_abbreviation)] = float(
            missing.prior_pra.sum() / denominator
        )

    rows: list[dict[str, object]] = []
    for row in games.itertuples(index=False):
        own = availability.get((row.game_id, row.team_abbreviation))
        opponent = availability.get((row.game_id, id_to_abbr.get(row.opponent_id)))
        if own is None or opponent is None:
            continue
        rows.append(
            {
                "game_id": row.game_id,
                "season": season,
                "team": row.team_abbreviation,
                "opp": id_to_abbr.get(row.opponent_id),
                "is_home": int(row.is_home),
                "margin": float(row.plus_minus),
                "missing_share": own,
                "opp_missing_share": opponent,
            }
        )
    return pd.DataFrame(rows)


def _design(frame: pd.DataFrame) -> pd.DataFrame:
    features = frame[["is_home", "missing_share", "opp_missing_share"]].copy()
    dummies = pd.get_dummies(
        frame[["team", "opp", "season"]], drop_first=True, dtype=float
    )
    return pd.concat([features, dummies], axis=1)


def main() -> None:
    team_games = pd.read_csv(TEAM_GAMES, parse_dates=["game_date"])
    logs = {season: _logs(season) for season in SEASONS}
    data = pd.concat(
        [
            _availability_table(team_games, logs, "2024-25", "2023-24"),
            _availability_table(team_games, logs, "2025-26", "2024-25"),
        ],
        ignore_index=True,
    )
    train = data[data.season == "2024-25"].copy()
    holdout = data[data.season == "2025-26"].copy()

    train_x = _design(train)
    holdout_x = _design(holdout).reindex(columns=train_x.columns, fill_value=0.0)
    model = LinearRegression().fit(train_x, train["margin"])
    prediction = model.predict(holdout_x)
    coefficient = {
        name: float(model.coef_[index])
        for index, name in enumerate(
            ["is_home", "missing_share", "opp_missing_share"]
        )
    }
    result = {
        "fit_season": "2024-25",
        "holdout_season": "2025-26",
        "fit_team_games": int(len(train)),
        "holdout_team_games": int(len(holdout)),
        "coefficient_points_per_share": -coefficient["missing_share"],
        "opponent_coefficient_points_per_share": coefficient["opp_missing_share"],
        "home_coefficient_points": coefficient["is_home"],
        "holdout_mae": float(mean_absolute_error(holdout["margin"], prediction)),
        "holdout_r2": float(r2_score(holdout["margin"], prediction)),
        "mean_missing_share": float(data.missing_share.mean()),
        "share_zero": float((data.missing_share == 0).mean()),
        "note": (
            "Regresion del margen con efectos fijos de equipo, rival y temporada. "
            "El coeficiente se aplica como 5.53 puntos de margen por fraccion "
            "completa del PRA del top-5 ausente."
        ),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

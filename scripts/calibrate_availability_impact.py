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
TEST_PREDICTIONS = ROOT / "reports" / "test_predictions.csv"
SEASONS = ("2023-24", "2024-25", "2025-26")
APPLIED_COEFFICIENT = 5.53


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


def _cluster_robust_se(
    frame: pd.DataFrame, design: pd.DataFrame, target: pd.Series
) -> np.ndarray:
    """Errores estándar agrupados por partido, con intercepto."""
    x = np.column_stack([np.ones(len(design)), design.to_numpy(dtype=float)])
    y = target.to_numpy(dtype=float)
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    residuals = y - x @ beta
    bread = np.linalg.pinv(x.T @ x)
    meat = np.zeros((x.shape[1], x.shape[1]))
    for indices in frame.groupby("game_id").indices.values():
        score = x[indices].T @ residuals[indices]
        meat += np.outer(score, score)
    covariance = bread @ meat @ bread
    return np.sqrt(np.diag(covariance))


def _applied_holdout(holdout: pd.DataFrame) -> dict[str, object] | None:
    """Compara margen base vs ajustado en las predicciones out-of-sample."""
    if not TEST_PREDICTIONS.exists():
        return None
    predictions = pd.read_csv(TEST_PREDICTIONS)
    if "actual_margin" not in predictions.columns:
        return None
    if "season" in predictions.columns:
        predictions = predictions[predictions["season"] == "2025-26"].copy()

    own = holdout[["game_id", "team", "missing_share"]].copy()
    home = own.rename(columns={"team": "home_team", "missing_share": "home_share"})
    away = own.rename(columns={"team": "away_team", "missing_share": "away_share"})
    merged = (
        predictions.merge(home, on=["game_id", "home_team"], how="inner")
        .merge(away, on=["game_id", "away_team"], how="inner")
        .dropna(subset=["home_share", "away_share", "predicted_margin", "actual_margin"])
    )
    if merged.empty:
        return None

    base = merged["predicted_margin"].to_numpy(dtype=float)
    actual = merged["actual_margin"].to_numpy(dtype=float)
    adjusted = base + APPLIED_COEFFICIENT * (
        merged["away_share"].to_numpy(dtype=float)
        - merged["home_share"].to_numpy(dtype=float)
    )
    return {
        "n_games": int(len(merged)),
        "mae_base": float(mean_absolute_error(actual, base)),
        "mae_adjusted": float(mean_absolute_error(actual, adjusted)),
        "r2_base": float(r2_score(actual, base)),
        "r2_adjusted": float(r2_score(actual, adjusted)),
        "winner_accuracy_base": float(np.mean((base > 0) == (actual > 0))),
        "winner_accuracy_adjusted": float(np.mean((adjusted > 0) == (actual > 0))),
        "changed_winner_predictions": int(np.sum((base > 0) != (adjusted > 0))),
    }


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
    fit_se = _cluster_robust_se(train, train_x, train["margin"])

    holdout_model = LinearRegression().fit(holdout_x, holdout["margin"])
    holdout_coefficient = {
        name: float(holdout_model.coef_[index])
        for index, name in enumerate(
            ["is_home", "missing_share", "opp_missing_share"]
        )
    }
    holdout_se = _cluster_robust_se(holdout, holdout_x, holdout["margin"])

    pooled_x = pd.concat([train_x, holdout_x], ignore_index=True)
    pooled_y = pd.concat(
        [train["margin"], holdout["margin"]], ignore_index=True
    )
    pooled_model = LinearRegression().fit(pooled_x, pooled_y)
    pooled_coefficient = {
        name: float(pooled_model.coef_[index])
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
        "fit_coefficient_standard_error": float(fit_se[2]),
        "opponent_coefficient_points_per_share": coefficient["opp_missing_share"],
        "home_coefficient_points": coefficient["is_home"],
        "holdout_coefficient_points_per_share": -holdout_coefficient["missing_share"],
        "holdout_coefficient_standard_error": float(holdout_se[2]),
        "pooled_coefficient_points_per_share": -pooled_coefficient["missing_share"],
        "holdout_mae": float(mean_absolute_error(holdout["margin"], prediction)),
        "holdout_r2": float(r2_score(holdout["margin"], prediction)),
        "applied_holdout": _applied_holdout(holdout),
        "mean_missing_share": float(data.missing_share.mean()),
        "share_zero": float((data.missing_share == 0).mean()),
        "note": (
            "Regresion del margen con efectos fijos de equipo, rival y temporada. "
            f"Se aplica {APPLIED_COEFFICIENT:.2f} puntos de margen por fraccion "
            "completa del PRA del top-5 ausente; el coeficiente aislado del "
            "holdout y la comparacion aplicada se reportan aparte."
        ),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

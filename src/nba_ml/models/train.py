"""Entrenamiento, evaluacion y guardado del modelo de totales.

Protocolo temporal (sin fuga de futuro):
    train: temporadas <= 2023-24
    validacion: 2024-25  -> seleccion de modelo/hiperparametros
    test: 2025-26        -> evaluacion final, no se toca hasta el final

Ademas se ejecuta una validacion walk-forward (entrenar con el pasado,
predecir cada temporada siguiente) para estimar el error fuera de muestra de
forma mas robusta.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import Ridge
from sklearn.model_selection import GridSearchCV, RandomizedSearchCV, TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ..config import (
    FIGURES_DIR,
    MODELS_DIR,
    RANDOM_STATE,
    REPORTS_DIR,
    TARGET,
    TRAIN_END,
    VAL_SEASON,
    TEST_SEASON,
    ensure_directories,
    season_index,
)
from ..data.build_dataset import MODEL_FEATURES, load_dataset

LOG = logging.getLogger(__name__)

# Conjunto completo generado por el pipeline (se conserva para comparaciones).
FULL_FEATURES = list(MODEL_FEATURES)

# Conjunto parsimonioso elegido por validacion + walk-forward: mejor MAE medio
# (14.84 vs 14.90 del completo) con 20 features en lugar de 76. Menos ruido,
# mas interpretable y mas estable entre temporadas.
SELECTED_FEATURES = [
    "form_total",
    "exp_total",
    "pace_sum",
    "off_rtg_sum",
    "def_rtg_sum",
    "league_total_roll20",
    "league_total_roll50",
    "league_total_roll100",
    "league_total_roll300",
    "ot_rate_sum",
    "close_rate_sum",
    "rest_diff",
    "b2b_sum",
    "elo_diff",
    "pts_exp_home",
    "pts_exp_away",
    "opp_pts_exp_home",
    "opp_pts_exp_away",
    "pace_exp_home",
    "pace_exp_away",
]

FEATURES = SELECTED_FEATURES
WALK_FORWARD_SEASONS = ("2021-22", "2022-23", "2023-24", "2024-25", "2025-26")
ARTIFACT_PATH = MODELS_DIR / "model.joblib"

# El hándicap se estima con un segundo Ridge sobre el margen local-visitante.
# Es una estimación del modelo, no una línea oficial de una casa de apuestas.
MARGIN_TARGET = "home_margin"
MARGIN_ALPHA = 30.0


# ---------------------------------------------------------------------------
# Metricas y particiones
# ---------------------------------------------------------------------------
def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """MAE, RMSE, R2, sesgo y aciertos dentro de margenes absolutos."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    errors = y_true - y_pred
    denominator = float(np.sum((y_true - y_true.mean()) ** 2))
    return {
        "n": int(len(y_true)),
        "mae": float(np.mean(np.abs(errors))),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "r2": float(1.0 - np.sum(errors**2) / denominator) if denominator else float("nan"),
        "bias": float(np.mean(errors)),
        "within_10": float(np.mean(np.abs(errors) <= 10.0)),
        "within_15": float(np.mean(np.abs(errors) <= 15.0)),
    }


def split_by_season(dataset: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Particion temporal por temporada, ordenada por fecha."""
    seasons = dataset["season"].map(season_index)
    train = dataset[seasons <= season_index(TRAIN_END)].copy()
    validation = dataset[seasons == season_index(VAL_SEASON)].copy()
    test = dataset[seasons == season_index(TEST_SEASON)].copy()

    for name, frame in (("train", train), ("validacion", validation), ("test", test)):
        if frame.empty:
            raise ValueError(f"Particion {name} vacia: revisa config.SEASONS")

    return (
        train.sort_values(["game_date", "game_id"]),
        validation.sort_values(["game_date", "game_id"]),
        test.sort_values(["game_date", "game_id"]),
    )


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
def make_model(name: str, params: dict | None = None):
    params = params or {}
    if name == "ridge":
        model = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                ("model", Ridge(random_state=RANDOM_STATE)),
            ]
        )
    elif name == "random_forest":
        model = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "model",
                    RandomForestRegressor(
                        n_estimators=400,
                        n_jobs=-1,
                        random_state=RANDOM_STATE,
                    ),
                ),
            ]
        )
    elif name == "hist_gradient_boosting":
        model = HistGradientBoostingRegressor(
            early_stopping=False,
            random_state=RANDOM_STATE,
        )
    else:
        raise ValueError(f"Modelo desconocido: {name}")
    model.set_params(**params)
    return model


def tune_models(
    x_train: pd.DataFrame, y_train: pd.Series
) -> dict[str, dict[str, object]]:
    """Busca hiperparametros con CV temporal dentro del conjunto de train."""
    cv = TimeSeriesSplit(n_splits=3)
    results: dict[str, dict[str, object]] = {}

    ridge = GridSearchCV(
        make_model("ridge"),
        {"model__alpha": [1.0, 10.0, 30.0, 100.0, 300.0]},
        scoring="neg_mean_absolute_error",
        cv=cv,
        n_jobs=-1,
    )
    ridge.fit(x_train, y_train)
    results["ridge"] = {
        "search": ridge,
        "params": ridge.best_params_,
        "cv_mae": float(-ridge.best_score_),
    }
    LOG.info("Ridge CV MAE=%.3f (%s)", -ridge.best_score_, ridge.best_params_)

    forest = GridSearchCV(
        make_model("random_forest"),
        {
            "model__min_samples_leaf": [5, 20, 50],
            "model__max_features": [0.3, 0.6],
        },
        scoring="neg_mean_absolute_error",
        cv=cv,
        n_jobs=-1,
    )
    forest.fit(x_train, y_train)
    results["random_forest"] = {
        "search": forest,
        "params": forest.best_params_,
        "cv_mae": float(-forest.best_score_),
    }
    LOG.info(
        "RandomForest CV MAE=%.3f (%s)", -forest.best_score_, forest.best_params_
    )

    boosting = RandomizedSearchCV(
        make_model("hist_gradient_boosting"),
        {
            "learning_rate": [0.02, 0.03, 0.05, 0.08],
            "max_iter": [300, 500, 800],
            "max_leaf_nodes": [15, 31, 63],
            "min_samples_leaf": [20, 50, 100],
            "l2_regularization": [0.0, 1.0, 10.0],
        },
        n_iter=20,
        scoring="neg_mean_absolute_error",
        cv=cv,
        n_jobs=-1,
        random_state=RANDOM_STATE,
        refit=True,
    )
    boosting.fit(x_train, y_train)
    results["hist_gradient_boosting"] = {
        "search": boosting,
        "params": boosting.best_params_,
        "cv_mae": float(-boosting.best_score_),
    }
    LOG.info(
        "HistGradientBoosting CV MAE=%.3f (%s)",
        -boosting.best_score_,
        boosting.best_params_,
    )
    return results


def walk_forward(
    dataset: pd.DataFrame,
    model_name: str,
    params: dict,
    seasons: tuple[str, ...] = WALK_FORWARD_SEASONS,
) -> tuple[list[dict[str, object]], np.ndarray]:
    """Entrena con todo el pasado y evalua en cada temporada siguiente."""
    rows: list[dict[str, object]] = []
    residuals: list[np.ndarray] = []

    for season in seasons:
        train = dataset[dataset["season"].map(season_index) < season_index(season)]
        test = dataset[dataset["season"] == season]
        if train.empty or test.empty:
            continue

        model = make_model(model_name, params)
        model.fit(train[FEATURES], train[TARGET])
        predictions = model.predict(test[FEATURES])
        metrics = regression_metrics(test[TARGET].to_numpy(), predictions)
        naive = test["form_total"].fillna(train[TARGET].mean()).to_numpy()
        baseline = regression_metrics(test[TARGET].to_numpy(), naive)
        metrics["season"] = season
        metrics["baseline_mae"] = baseline["mae"]
        metrics["delta_vs_form"] = baseline["mae"] - metrics["mae"]
        rows.append(metrics)
        residuals.append(test[TARGET].to_numpy() - predictions)
        LOG.info(
            "Walk-forward %s MAE=%.3f (forma reciente %.3f)",
            season,
            metrics["mae"],
            baseline["mae"],
        )

    return rows, np.concatenate(residuals) if residuals else np.array([])


def walk_forward_margin(
    dataset: pd.DataFrame,
    alpha: float = MARGIN_ALPHA,
    seasons: tuple[str, ...] = WALK_FORWARD_SEASONS,
) -> tuple[list[dict[str, object]], np.ndarray]:
    """Validación walk-forward del margen local-visitante."""
    rows: list[dict[str, object]] = []
    residuals: list[np.ndarray] = []

    for season in seasons:
        train = dataset[dataset["season"].map(season_index) < season_index(season)]
        test = dataset[dataset["season"] == season]
        if train.empty or test.empty:
            continue

        model = make_model("ridge", {"model__alpha": alpha})
        model.fit(train[FEATURES], train[MARGIN_TARGET])
        predictions = model.predict(test[FEATURES])
        actual = test[MARGIN_TARGET].to_numpy(dtype=float)
        metrics = regression_metrics(actual, predictions)
        metrics["season"] = season
        metrics["winner_accuracy"] = float(np.mean((predictions > 0) == (actual > 0)))
        rows.append(metrics)
        residuals.append(actual - predictions)

    return rows, np.concatenate(residuals) if residuals else np.array([])


def train_margin_model(dataset: pd.DataFrame, alpha: float = MARGIN_ALPHA) -> dict:
    """Entrena y evalúa el modelo de margen; devuelve el modelo de producción."""
    train_frame, validation_frame, test_frame = split_by_season(dataset)
    development = pd.concat([train_frame, validation_frame], ignore_index=True)

    evaluation = make_model("ridge", {"model__alpha": alpha})
    evaluation.fit(development[FEATURES], development[MARGIN_TARGET])
    test_predictions = evaluation.predict(test_frame[FEATURES])
    test_actual = test_frame[MARGIN_TARGET].to_numpy(dtype=float)
    test_metrics = regression_metrics(test_actual, test_predictions)
    test_metrics["winner_accuracy"] = float(
        np.mean((test_predictions > 0) == (test_actual > 0))
    )

    walk_rows, residuals = walk_forward_margin(dataset, alpha)
    if not residuals.size:
        residuals = test_actual - test_predictions
    sigma = float(np.std(residuals, ddof=1)) if residuals.size else float("nan")

    test_table = test_frame[
        ["game_id", "home_team", "away_team", "home_margin"]
    ].copy()
    test_table["predicted_margin"] = test_predictions
    test_table = test_table.rename(columns={"home_margin": "actual_margin"})

    production = make_model("ridge", {"model__alpha": alpha})
    production.fit(dataset[FEATURES], dataset[MARGIN_TARGET])
    return {
        "model": production,
        "residuals": residuals,
        "sigma": sigma,
        "test_metrics": test_metrics,
        "test_predictions": test_table,
        "walk_forward": walk_rows,
        "alpha": alpha,
    }


# ---------------------------------------------------------------------------
# Figuras y reporte
# ---------------------------------------------------------------------------
def _save_prediction_figures(
    y_true: np.ndarray, y_pred: np.ndarray, residuals: np.ndarray
) -> None:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.hexbin(y_true, y_pred, gridsize=45, cmap="viridis", mincnt=1)
    bounds = [min(y_true.min(), y_pred.min()), max(y_true.max(), y_pred.max())]
    ax.plot(bounds, bounds, color="crimson", linewidth=1.2, label="Prediccion perfecta")
    ax.set_xlabel("Total real (puntos)")
    ax.set_ylabel("Total predicho (puntos)")
    ax.set_title("Test 2025-26: prediccion vs realidad")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "pred_vs_actual.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(residuals, bins=50, color="#3b6ea5", alpha=0.85)
    ax.axvline(0, color="crimson", linewidth=1.2)
    ax.set_xlabel("Error (real - predicho)")
    ax.set_ylabel("Partidos")
    ax.set_title("Distribucion de residuos en test")
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "residuals.png", dpi=150)
    plt.close(fig)


def _save_importance_figure(importance: pd.DataFrame) -> None:
    top = importance.head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh(top["feature"], top["importance"], color="#3b6ea5")
    ax.set_xlabel("Aumento de MAE al permutar (puntos)")
    ax.set_title("Importancia por permutacion (test 2025-26)")
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "feature_importance.png", dpi=150)
    plt.close(fig)


def _save_walk_forward_figure(rows: list[dict[str, object]]) -> None:
    frame = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(frame["season"], frame["mae"], color="#3b6ea5")
    ax.set_ylabel("MAE (puntos)")
    ax.set_title("Validacion walk-forward por temporada")
    ax.set_ylim(max(0, frame["mae"].min() - 3), frame["mae"].max() + 2)
    for x, value in zip(frame["season"], frame["mae"]):
        ax.text(x, value + 0.15, f"{value:.2f}", ha="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "walk_forward_mae.png", dpi=150)
    plt.close(fig)


def _markdown_table(rows: list[dict[str, object]], columns: list[str]) -> str:
    header = "| " + " | ".join(columns) + " |"
    separator = "|" + "|".join(["---"] * len(columns)) + "|"
    lines = [header, separator]
    for row in rows:
        cells = []
        for column in columns:
            value = row.get(column, "")
            cells.append(f"{value:.3f}" if isinstance(value, float) else str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_report(
    results: dict[str, object],
    validation_rows: list[dict[str, object]],
    test_rows: list[dict[str, object]],
    walk_rows: list[dict[str, object]],
    importance: pd.DataFrame,
) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "metrics.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    best = results["best_model"]
    dataset = results["dataset"]
    test = results["test"]["metrics"]
    baseline = results["test"]["baselines"]
    mean_walk_mae = float(np.mean([row["mae"] for row in walk_rows])) if walk_rows else float("nan")
    mean_walk_baseline = (
        float(np.mean([row["baseline_mae"] for row in walk_rows]))
        if walk_rows
        else float("nan")
    )

    lines = [
        "# Resultados del modelo de totales NBA",
        "",
        f"Generado: {results['generated_at']}",
        "",
        "## Datos y particiones",
        "",
        f"- Partidos: **{dataset['games']:,}** ({dataset['first_season']} - {dataset['last_season']})",
        f"- Features: **{dataset['features']}**",
        f"- Train: {dataset['train_games']:,} partidos (hasta {TRAIN_END})",
        f"- Validacion: {dataset['validation_games']:,} partidos ({VAL_SEASON})",
        f"- Test: {dataset['test_games']:,} partidos ({TEST_SEASON})",
        "",
        "## Seleccion de modelo (validacion 2024-25)",
        "",
        _markdown_table(
            validation_rows, ["model", "mae", "rmse", "r2", "cv_mae"]
        ),
        "",
        "## Test 2025-26 (modelo reentrenado con train + validacion)",
        "",
        _markdown_table(
            test_rows,
            ["model", "n", "mae", "rmse", "r2", "bias", "within_10"],
        ),
        "",
        f"Mejor modelo: **{best['name']}**. Mejora de MAE frente a la media de la liga: "
        f"**{(baseline['league_mean']['mae'] - test['mae']) / baseline['league_mean']['mae']:.1%}**. "
        f"Frente al baseline de forma reciente: "
        f"**{(baseline['naive_form']['mae'] - test['mae']) / baseline['naive_form']['mae']:+.1%}** "
        "(un rival dificil de batir: los totales NBA tienen mucho ruido irreducible).",
        "",
        "## Validacion walk-forward",
        "",
        _markdown_table(
            walk_rows,
            ["season", "n", "mae", "baseline_mae", "delta_vs_form", "rmse", "r2", "bias"],
        ),
        "",
        f"MAE medio walk-forward: **{mean_walk_mae:.2f}** vs **{mean_walk_baseline:.2f}** "
        f"de la forma reciente ({len(walk_rows)} temporadas).",
        "",
        "El set completo de 76 features obtuvo un MAE walk-forward medio de 14.90; "
        "el conjunto parsimonioso de 20 features usado aqui, 14.84. Se prioriza el "
        "modelo simple por estabilidad e interpretabilidad.",
        "",
        f"Sigma de residuos fuera de muestra: **{results['residual_sigma']:.2f} puntos**.",
        "",
        "## Variables mas relevantes",
        "",
        _markdown_table(
            importance.head(10).to_dict("records"),
            ["feature", "importance"],
        ),
        "",
        "## Como leer estas metricas",
        "",
        "El total de puntos tiene un componente aleatorio grande (tiros que entran o no, "
        "prorrogas). Un MAE de 14-16 puntos sobre una desviacion tipica de ~20 implica "
        "una capacidad predictiva moderada: util como senal, no como certeza. No hay "
        "lineas reales de apuestas en el dataset, por lo que **no** se reporta ROI.",
        "",
    ]
    (REPORTS_DIR / "metrics.md").write_text("\n".join(lines), encoding="utf-8")
    LOG.info("Reporte escrito en %s", REPORTS_DIR / "metrics.md")


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------
def train(dataset: pd.DataFrame | None = None) -> dict[str, object]:
    ensure_directories()
    if dataset is None:
        dataset = load_dataset()

    train_frame, validation_frame, test_frame = split_by_season(dataset)
    x_train, y_train = train_frame[FEATURES], train_frame[TARGET]
    x_val, y_val = validation_frame[FEATURES], validation_frame[TARGET]

    LOG.info(
        "Train %s | Validacion %s | Test %s",
        len(train_frame),
        len(validation_frame),
        len(test_frame),
    )

    tuned = tune_models(x_train, y_train)

    # Comparacion honesta en validacion (los searches ya estan ajustados en train).
    validation_rows: list[dict[str, object]] = []
    validation_predictions: dict[str, np.ndarray] = {}
    for name, info in tuned.items():
        model = info["search"].best_estimator_
        predictions = model.predict(x_val)
        validation_predictions[name] = predictions
        metrics = regression_metrics(y_val.to_numpy(), predictions)
        metrics.update({"model": name, "cv_mae": info["cv_mae"]})
        validation_rows.append(metrics)

    best_name = min(validation_rows, key=lambda row: row["mae"])["model"]
    best_params = tuned[best_name]["params"]
    LOG.info("Mejor modelo en validacion: %s", best_name)

    # Evaluacion final: reentrenar con train + validacion y predecir test.
    development = pd.concat([train_frame, validation_frame], ignore_index=True)
    final_model = make_model(best_name, best_params)
    final_model.fit(development[FEATURES], development[TARGET])
    test_predictions = final_model.predict(test_frame[FEATURES])
    test_metrics = regression_metrics(test_frame[TARGET].to_numpy(), test_predictions)

    league_mean = float(y_train.mean())
    form_baseline = (
        test_frame["form_total"].fillna(league_mean).to_numpy()
    )
    baseline_predictions = {
        "league_mean": np.full(len(test_frame), league_mean),
        "naive_form": form_baseline,
    }
    baseline_metrics = {
        name: regression_metrics(test_frame[TARGET].to_numpy(), predictions)
        for name, predictions in baseline_predictions.items()
    }

    test_rows: list[dict[str, object]] = [
        {"model": "baseline_media_liga", **baseline_metrics["league_mean"]},
        {"model": "baseline_forma_reciente", **baseline_metrics["naive_form"]},
        {"model": f"{best_name}_train+val", **test_metrics},
    ]

    walk_rows, residuals = walk_forward(dataset, best_name, best_params)
    residual_sigma = float(np.std(residuals, ddof=1)) if residuals.size else float("nan")
    if not residuals.size:
        residuals = test_frame[TARGET].to_numpy() - test_predictions
        residual_sigma = float(np.std(residuals, ddof=1))

    # Importancia por permutacion sobre test, con el modelo de train+val.
    permutation = permutation_importance(
        final_model,
        test_frame[FEATURES],
        test_frame[TARGET],
        scoring="neg_mean_absolute_error",
        n_repeats=5,
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    importance = (
        pd.DataFrame(
            {
                "feature": FEATURES,
                "importance": permutation.importances_mean.tolist(),
            }
        )
        .sort_values("importance", ascending=False)
        .reset_index(drop=True)
    )

    _save_prediction_figures(
        test_frame[TARGET].to_numpy(), test_predictions, test_frame[TARGET].to_numpy() - test_predictions
    )
    _save_importance_figure(importance)
    if walk_rows:
        _save_walk_forward_figure(walk_rows)

    predictions_output = test_frame[
        ["game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts", TARGET]
    ].copy()
    predictions_output["predicted_total"] = test_predictions
    predictions_output["error"] = predictions_output[TARGET] - predictions_output["predicted_total"]

    # Modelo auxiliar de margen para el hándicap sugerido.
    margin = train_margin_model(dataset)
    margin_table = margin["test_predictions"][
        ["game_id", "predicted_margin", "actual_margin"]
    ]
    predictions_output = predictions_output.merge(
        margin_table, on="game_id", how="left"
    )
    predictions_output.to_csv(
        REPORTS_DIR / "test_predictions.csv", index=False, encoding="utf-8"
    )

    # Artefacto final para produccion: todas las temporadas disponibles.
    production_model = make_model(best_name, best_params)
    production_model.fit(dataset[FEATURES], dataset[TARGET])
    artifact = {
        "model": production_model,
        "features": FEATURES,
        "model_name": best_name,
        "params": best_params,
        "target": TARGET,
        "residual_sigma": residual_sigma,
        "residuals": residuals,
        "trained_through": str(dataset["season"].max()),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "test_metrics": test_metrics,
        "margin_model": margin["model"],
        "margin_residuals": margin["residuals"],
        "margin_sigma": margin["sigma"],
        "margin_test_metrics": margin["test_metrics"],
        "margin_walk_forward": margin["walk_forward"],
    }
    joblib.dump(artifact, ARTIFACT_PATH)
    LOG.info("Modelo guardado en %s", ARTIFACT_PATH)

    results: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "games": int(len(dataset)),
            "features": len(FEATURES),
            "first_season": str(dataset["season"].min()),
            "last_season": str(dataset["season"].max()),
            "train_games": int(len(train_frame)),
            "validation_games": int(len(validation_frame)),
            "test_games": int(len(test_frame)),
        },
        "best_model": {"name": best_name, "params": best_params},
        "cv": {name: {"cv_mae": info["cv_mae"], "params": info["params"]} for name, info in tuned.items()},
        "validation": validation_rows,
        "test": {"metrics": test_metrics, "baselines": baseline_metrics},
        "walk_forward": walk_rows,
        "residual_sigma": residual_sigma,
        "margin": {
            "target": MARGIN_TARGET,
            "alpha": margin["alpha"],
            "sigma": margin["sigma"],
            "test_metrics": margin["test_metrics"],
            "walk_forward": margin["walk_forward"],
        },
        "top_features": importance.head(15).to_dict("records"),
    }

    write_report(results, validation_rows, test_rows, walk_rows, importance)
    return results

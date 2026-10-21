"""Evaluacion de predicciones contra resultados reales (over/under).

La linea de referencia es la que introduce el usuario: nba_api no proporciona
lineas historicas de casas de apuestas. Sirve para comparar el sesgo del modelo
y su acierto direccional, no para afirmar que se habria batido el cierre.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..config import REPORTS_DIR


def load_test_predictions(path: str | Path | None = None) -> pd.DataFrame:
    """Predicciones fuera de muestra de la temporada de test."""
    predictions_path = Path(path) if path else REPORTS_DIR / "test_predictions.csv"
    if not predictions_path.exists():
        raise FileNotFoundError(
            f"No existe {predictions_path}. Ejecuta primero: nba-ml train"
        )
    return pd.read_csv(
        predictions_path,
        dtype={"game_id": str},
        parse_dates=["game_date"],
    )


def probability_over(
    predicted: pd.Series | np.ndarray,
    residuals: pd.Series | np.ndarray,
    line: float,
) -> np.ndarray:
    """P(total > linea) con la distribucion empirica de residuos."""
    predicted_array = np.asarray(predicted, dtype=float).reshape(-1)
    residuals_array = np.asarray(residuals, dtype=float).reshape(-1)
    if predicted_array.size == 0:
        return np.array([], dtype=float)
    return np.mean(
        residuals_array[None, :] > (float(line) - predicted_array)[:, None],
        axis=1,
    )


def add_over_under(
    frame: pd.DataFrame,
    line: float,
    residuals: pd.Series | np.ndarray,
    predicted_col: str = "predicted_total",
    total_col: str = "total_points",
) -> pd.DataFrame:
    """Añade resultado real, P(over), inclinacion del modelo y acierto."""
    result = frame.copy()
    predicted = pd.to_numeric(result[predicted_col], errors="coerce")
    total = pd.to_numeric(result[total_col], errors="coerce")

    result["line"] = float(line)
    actual = np.select(
        [total > line, total < line],
        ["OVER", "UNDER"],
        default="PUSH",
    )
    actual = pd.Series(actual, index=result.index, dtype="object")
    actual[total.isna()] = None
    result["actual_result"] = actual

    result["probability_over"] = np.nan
    valid = predicted.notna() & total.notna()
    if valid.any():
        result.loc[valid, "probability_over"] = probability_over(
            predicted[valid], residuals, line
        )

    lean = pd.Series("—", index=result.index, dtype="object")
    lean[result["probability_over"] >= 0.55] = "OVER"
    lean[result["probability_over"] <= 0.45] = "UNDER"
    result["model_lean"] = lean

    comparable = result["model_lean"].isin(["OVER", "UNDER"]) & result[
        "actual_result"
    ].isin(["OVER", "UNDER"])
    result["hit"] = np.where(
        comparable,
        (result["model_lean"] == result["actual_result"]).astype(float),
        np.nan,
    )
    return result


def summarize_results(frame: pd.DataFrame) -> dict[str, float | int]:
    """MAE, RMSE, sesgo, acierto direccional, tasa de over y Brier."""
    valid = frame.dropna(subset=["total_points", "predicted_total"])
    n = len(valid)
    if n == 0:
        return {
            "n": 0,
            "mae": float("nan"),
            "rmse": float("nan"),
            "bias": float("nan"),
            "hit_rate": float("nan"),
            "n_leans": 0,
            "over_rate": float("nan"),
            "brier": float("nan"),
        }

    error = valid["predicted_total"] - valid["total_points"]
    hits = valid["hit"].dropna()

    paired = valid.dropna(subset=["probability_over"])
    if len(paired):
        outcome = paired["actual_result"].eq("OVER").astype(float)
        brier = float(np.mean((paired["probability_over"] - outcome) ** 2))
    else:
        brier = float("nan")

    return {
        "n": int(n),
        "mae": float(error.abs().mean()),
        "rmse": float(np.sqrt(np.mean(error**2))),
        "bias": float(error.mean()),
        "hit_rate": float(hits.mean()) if len(hits) else float("nan"),
        "n_leans": int(len(hits)),
        "over_rate": float(valid["actual_result"].eq("OVER").mean()),
        "brier": brier,
    }

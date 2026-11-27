"""Métricas para predicciones registradas y resultados ya finalizados."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def evaluate_predictions(
    predictions: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Calcula métricas de total y mercados solo con partidos liquidados.

    Convención de signo: bias = predicción - resultado real. Para mercados,
    PUSH y picks sin dirección se excluyen del denominador de hit rate.
    """
    settled = [
        row
        for row in predictions
        if row.get("actual_home_score") is not None
        and row.get("actual_away_score") is not None
    ]
    errors: list[float] = []
    squared: list[float] = []
    total_hits: list[float] = []
    winner_hits: list[float] = []
    spread_hits: list[float] = []
    brier: list[float] = []

    for row in settled:
        actual_total = float(row["actual_home_score"]) + float(
            row["actual_away_score"]
        )
        error = float(row["predicted_total"]) - actual_total
        errors.append(abs(error))
        squared.append(error * error)

        line = row.get("market_total_line")
        if line is not None and actual_total != float(line):
            lean = "OVER" if float(row["predicted_total"]) > float(line) else "UNDER"
            actual = "OVER" if actual_total > float(line) else "UNDER"
            # Pick neutral si la proyección coincide exactamente con la línea.
            if float(row["predicted_total"]) != float(line):
                total_hits.append(float(lean == actual))

        margin = float(row["actual_home_score"]) - float(row["actual_away_score"])
        probability = row.get("predicted_home_win_probability")
        if probability is not None:
            brier.append((float(probability) - float(margin > 0)) ** 2)
        predicted_margin = row.get("predicted_margin")
        if predicted_margin is not None and margin != 0:
            winner_hits.append(float((float(predicted_margin) > 0) == (margin > 0)))

        handicap = row.get("home_handicap_line")
        if handicap is not None and margin + float(handicap) != 0:
            spread_hits.append(
                float((float(predicted_margin or 0) + float(handicap) > 0)
                      == (margin + float(handicap) > 0))
            )

    n = len(errors)
    return {
        "n_settled": n,
        "mae": _mean(errors),
        "rmse": math.sqrt(_mean(squared)) if squared else None,
        "bias": (
            _mean([
                float(row["predicted_total"])
                - float(row["actual_home_score"])
                - float(row["actual_away_score"])
                for row in settled
            ])
        ),
        "total_pick_count": len(total_hits),
        "total_hit_rate": _mean(total_hits),
        "winner_pick_count": len(winner_hits),
        "winner_hit_rate": _mean(winner_hits),
        "spread_pick_count": len(spread_hits),
        "spread_hit_rate": _mean(spread_hits),
        "brier_count": len(brier),
        "winner_brier": _mean(brier),
    }

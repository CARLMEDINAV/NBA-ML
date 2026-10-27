"""Probabilidades de mercado a partir de los residuos del modelo existente.

No se entrena un modelo nuevo. El total usa los residuos walk-forward del
modelo de totales; el ganador y el handicap usan los residuos del modelo de
margen. El total por equipo usa ambos residuos emparejados por partido.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .probability import validate_probability


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def empirical_over_probability(
    residuals: Any,
    projected: float,
    line: float,
) -> float | None:
    """P(real > linea) usando la distribucion empirica de residuos."""

    projection = _finite(projected)
    market_line = _finite(line)
    if projection is None or market_line is None:
        return None
    values = np.asarray(residuals, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return None
    probability = float(np.mean(values > (market_line - projection)))
    return validate_probability(probability)


def total_side_probability(
    artifact: dict[str, Any],
    predicted_total: float,
    line: float,
    side: str,
) -> float | None:
    """Probabilidad de Over/Under para el total del partido."""

    over = empirical_over_probability(
        artifact.get("residuals", []), predicted_total, line
    )
    if over is None:
        return None
    normalized = str(side).upper()
    if normalized == "OVER":
        return over
    if normalized == "UNDER":
        return 1.0 - over
    raise ValueError("El lado debe ser OVER o UNDER")


def paired_score_residuals(
    artifact: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray] | None:
    """Residuos historicos de puntos de local y visitante.

    Se derivan del total y del margen de la misma validacion walk-forward:
    local = (total + margen) / 2 y visitante = (total - margen) / 2. Si las
    longitudes no coinciden se usa el prefijo comun; esto evita inventar una
    dependencia entre partidos no alineados.
    """

    total = np.asarray(artifact.get("residuals", []), dtype=float)
    margin = np.asarray(artifact.get("margin_residuals", []), dtype=float)
    size = min(total.size, margin.size)
    if size == 0:
        return None
    total = total[:size]
    margin = margin[:size]
    paired = np.isfinite(total) & np.isfinite(margin)
    if not paired.any():
        return None
    total = total[paired]
    margin = margin[paired]
    return (total + margin) / 2.0, (total - margin) / 2.0


def team_total_side_probability(
    artifact: dict[str, Any],
    projected_home: float,
    projected_away: float,
    line: float,
    side: str,
    venue: str,
) -> float | None:
    """Probabilidad de Over/Under para el total de un equipo.

    La linea es la introducida por el usuario; no se presenta como una linea
    de mercado.
    """

    residuals = paired_score_residuals(artifact)
    if residuals is None:
        return None
    home_residuals, away_residuals = residuals
    normalized_venue = str(venue).lower()
    if normalized_venue == "home":
        projected = projected_home
        values = home_residuals
    elif normalized_venue == "away":
        projected = projected_away
        values = away_residuals
    else:
        raise ValueError("El equipo debe ser home o away")
    over = empirical_over_probability(values, projected, line)
    if over is None:
        return None
    normalized_side = str(side).upper()
    if normalized_side == "OVER":
        return over
    if normalized_side == "UNDER":
        return 1.0 - over
    raise ValueError("El lado debe ser OVER o UNDER")


def winner_side_probability(
    home_probability: float | None,
    side: str,
) -> float | None:
    """Probabilidad de que gane el local o el visitante."""

    if home_probability is None:
        return None
    home = validate_probability(home_probability, name="P(gana local)")
    normalized = str(side).upper()
    if normalized == "HOME":
        return home
    if normalized == "AWAY":
        return 1.0 - home
    raise ValueError("El lado debe ser HOME o AWAY")


def handicap_side_probability(
    home_cover_probability: float | None,
    side: str,
) -> float | None:
    """Probabilidad de que el local o el visitante cubra el handicap."""

    if home_cover_probability is None:
        return None
    home = validate_probability(home_cover_probability, name="P(cubre local)")
    normalized = str(side).upper()
    if normalized == "HOME":
        return home
    if normalized == "AWAY":
        return 1.0 - home
    raise ValueError("El lado debe ser HOME o AWAY")

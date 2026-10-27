"""Calculos puros para el Bet Builder.

Todas las funciones validan sus entradas. Las probabilidades deben estar en
[0, 1] y las cuotas decimales deben ser mayores que 1. No se aplican ajustes
arbitrarios: la probabilidad conjunta solo se multiplica cuando el llamador
declara independencia entre selecciones.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

MIN_DECIMAL_ODDS = 1.0
MAX_DECIMAL_ODDS = 1000.0


def validate_probability(value: float, *, name: str = "probabilidad") -> float:
    """Devuelve una probabilidad valida o lanza ``ValueError``."""

    try:
        probability = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} no es numerica") from error
    if not math.isfinite(probability):
        raise ValueError(f"{name} no es finita")
    if probability < 0.0 or probability > 1.0:
        raise ValueError(f"{name} debe estar entre 0 y 1")
    return probability


def validate_decimal_odds(value: float, *, name: str = "cuota decimal") -> float:
    """Valida una cuota decimal mayor que 1 y razonable para la UI."""

    try:
        odds = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} no es numerica") from error
    if not math.isfinite(odds):
        raise ValueError(f"{name} no es finita")
    if odds <= MIN_DECIMAL_ODDS or odds > MAX_DECIMAL_ODDS:
        raise ValueError(
            f"{name} debe ser mayor que 1 y menor o igual que {MAX_DECIMAL_ODDS:g}"
        )
    return odds


def validate_stake(value: float, *, name: str = "stake") -> float:
    """Valida un importe positivo para el retorno ilustrativo."""

    try:
        stake = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} no es numerico") from error
    if not math.isfinite(stake) or stake <= 0:
        raise ValueError(f"{name} debe ser mayor que 0")
    return stake


def combine_independent(probabilities: Iterable[float]) -> float | None:
    """Multiplica probabilidades bajo un supuesto explicito de independencia.

    Devuelve ``None`` si no hay selecciones. No se debe usar para selecciones
    del mismo partido cuando no existe un modelo de dependencia validado.
    """

    values = [validate_probability(value) for value in probabilities]
    if not values:
        return None
    combined = 1.0
    for probability in values:
        combined *= probability
    return min(1.0, max(0.0, combined))


def fair_decimal_odds(probability: float) -> float | None:
    """Cuota justa teorica (1 / p), sin margen de casa."""

    value = validate_probability(probability)
    if value <= 0.0:
        return None
    return 1.0 / value


def expected_value(probability: float, decimal_odds: float) -> float:
    """Retorno esperado por unidad: ``p * O - 1``."""

    value = validate_probability(probability)
    odds = validate_decimal_odds(decimal_odds)
    return value * odds - 1.0


def potential_return(stake: float, decimal_odds: float) -> float:
    """Retorno bruto potencial para un stake y una cuota decimal."""

    amount = validate_stake(stake)
    odds = validate_decimal_odds(decimal_odds)
    return amount * odds


def american_to_decimal(price: float) -> float:
    """Convierte una cuota americana valida a cuota decimal."""

    try:
        american = float(price)
    except (TypeError, ValueError) as error:
        raise ValueError("cuota americana no numerica") from error
    if not math.isfinite(american) or american == 0:
        raise ValueError("cuota americana invalida")
    if abs(american) < 100:
        raise ValueError("la cuota americana debe ser >= 100 o <= -100")
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)

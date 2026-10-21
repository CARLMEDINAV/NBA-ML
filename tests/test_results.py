"""Tests de la evaluacion over/under contra resultados reales."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nba_ml.models.results import (
    add_over_under,
    load_test_predictions,
    probability_over,
    summarize_results,
)


def test_probability_over_uses_empirical_residuals():
    probabilities = probability_over([225, 235], [-10, 0, 10], 225.5)

    assert np.allclose(probabilities, [1 / 3, 2 / 3])


def test_probability_over_empty_input():
    assert probability_over([], [-10, 0, 10], 225.5).size == 0


def test_add_over_under_computes_result_lean_and_hit():
    frame = pd.DataFrame(
        {
            "total_points": [230, 220, 225.5, 240],
            "predicted_total": [228, 226, 230, 232],
        }
    )
    result = add_over_under(frame, 225.5, np.array([-10, 0, 10]))

    assert result["actual_result"].tolist() == ["OVER", "UNDER", "PUSH", "OVER"]
    assert result["model_lean"].tolist() == ["OVER", "OVER", "OVER", "OVER"]
    assert result["hit"].iloc[0] == 1.0
    assert result["hit"].iloc[1] == 0.0
    assert pd.isna(result["hit"].iloc[2])
    assert result["hit"].iloc[3] == 1.0


def test_summary_results_metrics():
    frame = pd.DataFrame(
        {
            "total_points": [230, 220, 225.5, 240],
            "predicted_total": [228, 226, 230, 232],
        }
    )
    summary = summarize_results(
        add_over_under(frame, 225.5, np.array([-10, 0, 10]))
    )

    assert summary["n"] == 4
    assert summary["mae"] == pytest.approx(5.125)
    assert summary["rmse"] == pytest.approx(np.sqrt(31.0625))
    assert summary["bias"] == pytest.approx(0.125)
    assert summary["hit_rate"] == pytest.approx(2 / 3)
    assert summary["n_leans"] == 3
    assert summary["over_rate"] == pytest.approx(0.5)
    assert summary["brier"] == pytest.approx(5 / 18)


def test_load_test_predictions_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_test_predictions(tmp_path / "no_existe.csv")

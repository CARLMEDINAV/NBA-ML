"""Añade el modelo de margen/hándicap a los artefactos existentes.

No reentrena el modelo de totales: entrena el Ridge auxiliar de margen con el
mismo protocolo temporal y actualiza ``models/model.joblib`` y
``artifacts/model.joblib``. Se puede volver a ejecutar sin problema.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nba_ml.data.build_dataset import load_dataset  # noqa: E402
from nba_ml.models.train import train_margin_model  # noqa: E402

MODEL_PATH = ROOT / "models" / "model.joblib"
ARTIFACT_PATH = ROOT / "artifacts" / "model.joblib"
TEST_PREDICTIONS_PATH = ROOT / "reports" / "test_predictions.csv"
ARTIFACT_TEST_PREDICTIONS_PATH = ROOT / "artifacts" / "test_predictions.csv"


def main() -> int:
    dataset = load_dataset()
    margin = train_margin_model(dataset)

    artifact = joblib.load(MODEL_PATH)
    artifact.update(
        {
            "margin_model": margin["model"],
            "margin_residuals": margin["residuals"],
            "margin_sigma": margin["sigma"],
            "margin_test_metrics": margin["test_metrics"],
            "margin_walk_forward": margin["walk_forward"],
        }
    )
    joblib.dump(artifact, MODEL_PATH)
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MODEL_PATH, ARTIFACT_PATH)

    if TEST_PREDICTIONS_PATH.exists():
        predictions = pd.read_csv(TEST_PREDICTIONS_PATH, dtype={"game_id": str})
        margin_table = margin["test_predictions"].copy()
        margin_table["game_id"] = margin_table["game_id"].astype(str)
        predictions = predictions.drop(
            columns=["predicted_margin", "actual_margin"], errors="ignore"
        )
        predictions = predictions.merge(
            margin_table[["game_id", "predicted_margin", "actual_margin"]],
            on="game_id",
            how="left",
        )
        predictions.to_csv(TEST_PREDICTIONS_PATH, index=False)
        ARTIFACT_TEST_PREDICTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(TEST_PREDICTIONS_PATH, ARTIFACT_TEST_PREDICTIONS_PATH)
        print(
            "Añadido margen out-of-sample a "
            f"{TEST_PREDICTIONS_PATH.relative_to(ROOT)} y "
            f"{ARTIFACT_TEST_PREDICTIONS_PATH.relative_to(ROOT)}"
        )

    metrics = margin["test_metrics"]
    print(
        "Margen test: "
        f"MAE={metrics['mae']:.2f}, RMSE={metrics['rmse']:.2f}, "
        f"R2={metrics['r2']:.3f}, "
        f"acierto ganador={metrics['winner_accuracy']:.1%}"
    )
    print(f"Sigma walk-forward={margin['sigma']:.2f}")
    print(f"Actualizado {MODEL_PATH.relative_to(ROOT)}")
    print(f"Actualizado {ARTIFACT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

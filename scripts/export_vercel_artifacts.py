r"""Copia a ``artifacts/`` la foto minima que necesita la app de Vercel.

Vercel no puede escribir en disco ni conservar la cache de ``data/``. Por eso
versionamos solo el modelo, el estado de los equipos, el calendario, las
estadisticas de jugadores y las predicciones de test. Este script se ejecuta
despues de actualizar datos/entrenar y antes de hacer commit:

    .\.venv\Scripts\python.exe scripts\export_vercel_artifacts.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"

FILES = {
    ROOT / "models" / "model.joblib": ARTIFACTS / "model.joblib",
    ROOT / "models" / "player_props.joblib": ARTIFACTS / "player_props.joblib",
    ROOT / "data" / "processed" / "team_state.csv": ARTIFACTS / "team_state.csv",
    ROOT / "data" / "raw" / "schedule_2026_27.csv": ARTIFACTS / "schedule_2026_27.csv",
    ROOT / "reports" / "test_predictions.csv": ARTIFACTS / "test_predictions.csv",
    ROOT / "data" / "raw" / "player_stats_2025_26_pergame.csv": ARTIFACTS
    / "player_stats_2025_26_pergame.csv",
    ROOT / "data" / "raw" / "player_stats_2025_26_totals.csv": ARTIFACTS
    / "player_stats_2025_26_totals.csv",
    ROOT / "data" / "raw" / "player_stats_2026_27_pergame.csv": ARTIFACTS
    / "player_stats_2026_27_pergame.csv",
    ROOT / "data" / "raw" / "player_stats_2026_27_totals.csv": ARTIFACTS
    / "player_stats_2026_27_totals.csv",
}


def main() -> int:
    missing = [str(source) for source in FILES if not source.exists()]
    if missing:
        print("Faltan artefactos:")
        for path in missing:
            print(f"  - {path}")
        print("Ejecuta antes: nba-ml download / build / train")
        return 1

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for source, destination in FILES.items():
        shutil.copy2(source, destination)
        size_kb = destination.stat().st_size / 1024
        print(f"{destination.relative_to(ROOT)}  {size_kb:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

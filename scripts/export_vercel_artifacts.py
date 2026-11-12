r"""Copia a ``artifacts/`` la foto versionada que inicializa la API de Render.

Render conserva los artefactos de produccion en un disco persistente. Esta
foto versionada sirve como semilla inicial y para sincronizar modelos y
backtests entrenados en cada deploy. Ejecuta este script despues de actualizar
datos o entrenar:

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

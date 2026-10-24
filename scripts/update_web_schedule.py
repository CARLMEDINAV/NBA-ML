"""Actualiza el calendario/resultados que consume la web de Vercel.

Se ejecuta desde GitHub Actions cada pocos minutos durante la temporada. Solo
copia el CSV cuando hay finales nuevos o cambios en el calendario; asi no crea
un commit por cada cambio de cuarto en un partido en juego.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nba_ml.data.schedule import load_schedule, schedule_path  # noqa: E402

SEASON = "2026-27"
DESTINATION = ROOT / "artifacts" / "schedule_2026_27.csv"
RESULT_COLUMNS = [
    "game_id",
    "game_date",
    "home",
    "away",
    "home_score",
    "away_score",
    "total_points",
]


def _finals(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [column for column in RESULT_COLUMNS if column in frame.columns]
    finals = frame[frame["status"].eq(3)][columns].copy()
    if "game_id" in finals.columns:
        finals["game_id"] = finals["game_id"].astype(str)
    if "game_date" in finals.columns:
        finals["game_date"] = pd.to_datetime(finals["game_date"]).dt.strftime("%Y-%m-%d")
    if "game_id" in finals.columns:
        finals = finals.sort_values("game_id").reset_index(drop=True)
    return finals


def main() -> int:
    try:
        frame = load_schedule(SEASON, refresh=True)
    except Exception as error:  # noqa: BLE001 - no romper el workflow por un timeout
        message = f"No se pudo actualizar el calendario: {error}"
        print(f"::warning::{message}")
        if DESTINATION.exists():
            print("Se conserva el calendario/resultados anterior.")
            return 0
        return 1

    source = schedule_path(SEASON)
    finals = int((frame["status"] == 3).sum())
    live = int((frame["status"] == 2).sum())
    print(f"{SEASON}: {len(frame)} partidos, {finals} finalizados, {live} en juego")

    if DESTINATION.exists():
        previous = pd.read_csv(DESTINATION, dtype={"game_id": str})
        if _finals(previous).equals(_finals(frame)):
            print("Sin finales nuevos; no se actualiza el artefacto.")
            return 0

    shutil.copy2(source, DESTINATION)
    print(f"Actualizado {DESTINATION.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

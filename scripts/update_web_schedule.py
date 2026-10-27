"""Actualiza el calendario/resultados que consume la web de Vercel.

Se ejecuta desde GitHub Actions cada pocos minutos durante la temporada. Los
runners no alcanzan ``stats.nba.com``, asi que se consultan los marcadores de
ESPN y se fusionan con el calendario completo ya publicado. Solo se copia el
CSV cuando hay finales nuevos o cambios estructurales; los cambios de cuarto
en un partido en juego no generan un commit.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nba_ml.data.espn import update_schedule_from_espn  # noqa: E402
from nba_ml.data.schedule import schedule_path  # noqa: E402

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
STRUCTURE_COLUMNS = [
    "game_id",
    "game_date",
    "game_time_et",
    "home",
    "away",
    "arena",
    "is_neutral",
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


def _structure(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [column for column in STRUCTURE_COLUMNS if column in frame.columns]
    structure = frame[columns].copy()
    if "game_id" in structure.columns:
        structure["game_id"] = structure["game_id"].astype(str)
    if "game_date" in structure.columns:
        structure["game_date"] = pd.to_datetime(
            structure["game_date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
    return structure.sort_values(
        ["game_date", "game_time_et", "game_id"], kind="stable"
    ).reset_index(drop=True)


def _unchanged(previous: pd.DataFrame, current: pd.DataFrame) -> bool:
    return _finals(previous).equals(_finals(current)) and _structure(
        previous
    ).equals(_structure(current))


def main() -> int:
    previous: pd.DataFrame | None = None
    if DESTINATION.exists():
        previous = pd.read_csv(DESTINATION, dtype={"game_id": str})

    try:
        frame = update_schedule_from_espn(
            SEASON,
            previous,
            days_back=7,
            days_forward=3,
        )
    except Exception as error:  # noqa: BLE001 - no romper el workflow por red
        message = f"No se pudo actualizar el calendario desde ESPN: {error}"
        print(f"::warning::{message}")
        if DESTINATION.exists():
            print("Se conserva el calendario/resultados anterior.")
            return 0
        return 1

    if frame.empty:
        print("::warning::ESPN no devolvio partidos para el calendario.")
        return 0 if DESTINATION.exists() else 1

    source = schedule_path(SEASON)
    source.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(source, index=False)

    finals = int((frame["status"] == 3).sum())
    live = int((frame["status"] == 2).sum())
    print(f"{SEASON}: {len(frame)} partidos, {finals} finalizados, {live} en juego")

    if previous is not None and _unchanged(previous, frame):
        print("Sin finales nuevos; no se actualiza el artefacto.")
        return 0

    shutil.copy2(source, DESTINATION)
    print(f"Actualizado {DESTINATION.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

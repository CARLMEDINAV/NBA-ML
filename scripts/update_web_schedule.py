"""Actualiza el calendario/resultados que consume la web de Vercel.

Se ejecuta desde GitHub Actions cada pocos minutos durante la temporada. Si la
NBA ya publicó marcadores nuevos, el CSV cambia y el workflow hace commit para
que Vercel vuelva a desplegar. Si no cambia nada, no hay commit.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nba_ml.data.schedule import load_schedule, schedule_path  # noqa: E402

SEASON = "2026-27"
DESTINATION = ROOT / "artifacts" / "schedule_2026_27.csv"


def main() -> int:
    frame = load_schedule(SEASON, refresh=True)
    source = schedule_path(SEASON)
    shutil.copy2(source, DESTINATION)
    finals = int((frame["status"] == 3).sum())
    live = int((frame["status"] == 2).sum())
    print(f"{SEASON}: {len(frame)} partidos, {finals} finalizados, {live} en juego")
    print(f"Actualizado {DESTINATION.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

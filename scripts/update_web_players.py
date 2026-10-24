"""Actualiza las estadisticas de jugadores que consume la web de Vercel.

Se ejecuta desde GitHub Actions unas pocas veces al dia. Vercel no puede
consultar ``stats.nba.com``, asi que la app publica lee la foto de ``artifacts``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nba_ml.data.players import load_player_stats, player_stats_path  # noqa: E402

SEASON = "2026-27"
MODES = {"PerGame": "pergame", "Totals": "totals"}


def _destinations() -> list[Path]:
    return [
        ROOT / "artifacts" / f"player_stats_{SEASON.replace('-', '_')}_{suffix}.csv"
        for suffix in MODES.values()
    ]


def main() -> int:
    frames = {}
    for per_mode in MODES:
        try:
            frames[per_mode] = load_player_stats(
                SEASON,
                per_mode=per_mode,
                refresh=True,
                timeout=120,
            )
        except Exception as error:  # noqa: BLE001 - no romper el workflow por un timeout
            print(
                f"::warning::No se pudieron actualizar las estadisticas "
                f"({per_mode}) de {SEASON}: {error}"
            )
            if all(path.exists() for path in _destinations()):
                print("Se conservan las estadisticas de jugadores anteriores.")
                return 0
            return 1

    for per_mode, suffix in MODES.items():
        frame = frames[per_mode]
        source = player_stats_path(SEASON, per_mode)
        destination = (
            ROOT / "artifacts" / f"player_stats_{SEASON.replace('-', '_')}_{suffix}.csv"
        )
        shutil.copy2(source, destination)
        print(
            f"{destination.relative_to(ROOT)}: {len(frame)} jugadores, "
            f"{int(frame['gp'].max())} partidos maximos"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

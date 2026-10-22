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


def main() -> int:
    for per_mode, suffix in MODES.items():
        frame = load_player_stats(
            SEASON,
            per_mode=per_mode,
            refresh=True,
            timeout=90,
        )
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

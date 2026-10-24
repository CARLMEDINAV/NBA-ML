"""Actualiza las estadisticas de jugadores que consume la web de Vercel.

Se ejecuta desde GitHub Actions unas pocas veces al dia. Vercel no puede
consultar ``stats.nba.com``, asi que la app publica lee la foto de ``artifacts``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nba_ml.data.players import load_player_stats, player_stats_path  # noqa: E402

SEASON = "2026-27"
MODES = {"PerGame": "pergame", "Totals": "totals"}
BIO = ROOT / "artifacts" / "player_bio.csv"


def _destinations() -> list[Path]:
    return [
        ROOT / "artifacts" / f"player_stats_{SEASON.replace('-', '_')}_{suffix}.csv"
        for suffix in MODES.values()
    ]


def _update_bio() -> None:
    """Altura actual para estimar el rol defensivo sin depender de posiciones."""
    try:
        from nba_api.stats.endpoints import leaguedashplayerbiostats

        raw = leaguedashplayerbiostats.LeagueDashPlayerBioStats(
            season=SEASON,
            season_type_all_star="Regular Season",
            timeout=120,
        ).get_data_frames()[0]
    except Exception as error:  # noqa: BLE001 - no bloquear las estadisticas
        print(f"::warning::No se pudo actualizar {BIO.name}: {error}")
        return

    frame = raw.rename(
        columns={
            "PLAYER_ID": "player_id",
            "PLAYER_NAME": "player_name",
            "TEAM_ABBREVIATION": "team",
            "PLAYER_HEIGHT_INCHES": "height_inches",
            "PLAYER_WEIGHT": "weight",
        }
    )
    required = {"player_id", "player_name", "team", "height_inches", "weight"}
    if not required.issubset(frame.columns):
        print(f"::warning::{BIO.name}: faltan columnas de biografia")
        return
    frame = frame[
        ["player_id", "player_name", "team", "height_inches", "weight"]
    ].copy()
    frame["player_id"] = pd.to_numeric(frame["player_id"], errors="coerce")
    frame["height_inches"] = pd.to_numeric(
        frame["height_inches"], errors="coerce"
    )
    frame["weight"] = pd.to_numeric(frame["weight"], errors="coerce")
    frame["player_name"] = frame["player_name"].fillna("").astype(str).str.strip()
    frame["team"] = (
        frame["team"].fillna("").astype(str).str.upper().str.strip()
    )
    frame = frame.dropna(subset=["player_id", "height_inches"])
    frame = frame[
        frame["player_name"].ne("")
        & frame["team"].ne("")
        & frame["height_inches"].gt(0)
    ].copy()
    frame["player_id"] = frame["player_id"].astype(int)
    frame = frame.sort_values("player_id")
    temporary = BIO.with_suffix(".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(BIO)
    print(f"{BIO.relative_to(ROOT)}: {len(frame)} jugadores")


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
    _update_bio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

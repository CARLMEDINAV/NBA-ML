"""Actualiza las estadisticas de jugadores que consume la web de Vercel.

Se ejecuta desde GitHub Actions unas pocas veces al dia. Vercel no puede
consultar ``stats.nba.com`` y los runners de GitHub tampoco lo alcanzan de
forma fiable, asi que la actualizacion usa ESPN como fuente alternativa.

La foto publicada sigue teniendo el mismo esquema que la descarga original de
la NBA para que la app no necesite cambios.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nba_ml.data.espn import fetch_player_stats  # noqa: E402
from nba_ml.data.players import player_stats_path  # noqa: E402

SEASON = "2026-27"
MODES = {"PerGame": "pergame", "Totals": "totals"}
BIO = ROOT / "artifacts" / "player_bio.csv"


def _destinations() -> list[Path]:
    return [
        ROOT / "artifacts" / f"player_stats_{SEASON.replace('-', '_')}_{suffix}.csv"
        for suffix in MODES.values()
    ]


def _update_bio() -> None:
    """Altura actual para estimar el rol defensivo sin depender de posiciones.

    La biografia no tiene equivalente directo en el resumen de ESPN; si la NBA
    no responde se conserva la foto anterior. En GitHub se omite para no perder
    minutos esperando un timeout conocido.
    """
    if os.getenv("GITHUB_ACTIONS") and os.getenv("NBA_BIO_UPDATE", "0") != "1":
        print(f"{BIO.name}: se conserva la biografia anterior en GitHub Actions")
        return
    try:
        from nba_api.stats.endpoints import leaguedashplayerbiostats

        raw = leaguedashplayerbiostats.LeagueDashPlayerBioStats(
            season=SEASON,
            season_type_all_star="Regular Season",
            timeout=int(os.getenv("NBA_BIO_TIMEOUT", "30")),
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
    try:
        pergame, totals = fetch_player_stats(SEASON)
    except Exception as error:  # noqa: BLE001 - no romper el workflow por red
        print(
            f"::warning::No se pudieron actualizar las estadisticas de "
            f"{SEASON} desde ESPN: {error}"
        )
        if all(path.exists() for path in _destinations()):
            print("Se conservan las estadisticas de jugadores anteriores.")
            return 0
        return 1

    if pergame.empty or totals.empty:
        print("::warning::ESPN no devolvio estadisticas de jugadores.")
        return 0 if all(path.exists() for path in _destinations()) else 1

    for per_mode, frame in (("PerGame", pergame), ("Totals", totals)):
        suffix = MODES[per_mode]
        destination = (
            ROOT / "artifacts" / f"player_stats_{SEASON.replace('-', '_')}_{suffix}.csv"
        )
        source = player_stats_path(SEASON, per_mode)
        source.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(source, index=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        print(
            f"{destination.relative_to(ROOT)}: {len(frame)} jugadores, "
            f"{int(frame['gp'].max())} partidos maximos"
        )

    _update_bio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Inicializa el almacenamiento persistente y arranca FastAPI en Render."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ARTIFACTS = ROOT / "artifacts"
PERSISTENT_ARTIFACTS = Path(
    os.getenv("NBA_ARTIFACTS_DIR", "/var/data/artifacts")
).resolve()
# Modelos y backtests versionados se sincronizan en cada deploy. Los CSV de
# temporada en curso e historiales actualizados por el scheduler se preservan.
SYNC_ON_DEPLOY = {
    "model.joblib",
    "player_props.joblib",
    "test_predictions.csv",
    "player_stats_2025_26_pergame.csv",
    "player_stats_2025_26_totals.csv",
}


def prepare_artifacts() -> None:
    PERSISTENT_ARTIFACTS.mkdir(parents=True, exist_ok=True)
    SOURCE_ARTIFACTS.mkdir(parents=True, exist_ok=True)

    # Copia los artefactos versionados solo cuando falten en el disco.
    # Los datos actualizados en producción nunca se sobrescriben al desplegar.
    for source in SOURCE_ARTIFACTS.iterdir():
        destination = PERSISTENT_ARTIFACTS / source.name
        if source.is_file() and (
            not destination.exists() or source.name in SYNC_ON_DEPLOY
        ):
            shutil.copy2(source, destination)

    if SOURCE_ARTIFACTS.is_symlink():
        if SOURCE_ARTIFACTS.resolve() == PERSISTENT_ARTIFACTS:
            return
        SOURCE_ARTIFACTS.unlink()
    elif SOURCE_ARTIFACTS.exists():
        shutil.rmtree(SOURCE_ARTIFACTS)

    SOURCE_ARTIFACTS.symlink_to(PERSISTENT_ARTIFACTS, target_is_directory=True)


if __name__ == "__main__":
    prepare_artifacts()
    os.chdir(ROOT)
    port = os.getenv("PORT", "10000")
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
            "--proxy-headers",
        ],
    )

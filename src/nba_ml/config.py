"""Configuracion central del proyecto.

Todas las rutas son absolutas y se derivan de la ubicacion del paquete, de modo
que los comandos funcionan igual desde cualquier directorio de trabajo.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

MODELS_DIR = PROJECT_ROOT / "models"
REPORTS_DIR = PROJECT_ROOT / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"

# Temporadas regulares descargadas. 2025-26 es la ultima completa a la fecha.
SEASONS: tuple[str, ...] = tuple(
    f"{year}-{str(year + 1)[-2:]}" for year in range(2015, 2026)
)
SEASON_TYPE = "Regular Season"

# Ventanas moviles (en partidos) usadas para construir features.
SHORT_WINDOW = 5
LONG_WINDOW = 10

# Particion temporal: nunca se entrena con temporadas futuras.
TRAIN_END = "2023-24"
VAL_SEASON = "2024-25"
TEST_SEASON = "2025-26"

TARGET = "total_points"
RANDOM_STATE = 42

# Parametros de Elo (compartidos entre entrenamiento y prediccion).
ELO_BASE = 1500.0
ELO_HOME_ADVANTAGE = 100.0
ELO_K = 20.0
ELO_SEASON_CARRYOVER = 0.75

# Columnas que identifican un partido o contienen informacion posterior al
# partido. Nunca deben entrar como features.
META_COLUMNS = [
    "game_id",
    "game_date",
    "season",
    "home_team",
    "away_team",
    "home_team_id",
    "away_team_id",
    "home_pts",
    "away_pts",
    TARGET,
    "home_margin",
    "home_win",
    "is_overtime",
    "elo_post_home",
    "elo_post_away",
]


def season_index(season: str) -> int:
    """Devuelve el anio de inicio de una temporada ('2024-25' -> 2024)."""
    return int(season.split("-")[0])


def ensure_directories() -> None:
    for directory in (RAW_DIR, PROCESSED_DIR, MODELS_DIR, REPORTS_DIR, FIGURES_DIR):
        directory.mkdir(parents=True, exist_ok=True)

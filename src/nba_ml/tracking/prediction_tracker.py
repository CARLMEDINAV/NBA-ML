"""Registro inmutable de predicciones prepartido y sincronización de resultados.

SQLite se usa sin dependencias adicionales. En producción serverless, configura
una ruta persistente montada o sustituye este repositorio por PostgreSQL; el
filesystem efímero de Vercel NO es almacenamiento persistente.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id TEXT PRIMARY KEY,
    game_id TEXT NOT NULL,
    game_date TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    predicted_at TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    predicted_total REAL NOT NULL,
    market_total_line REAL,
    predicted_margin REAL,
    home_handicap_line REAL,
    predicted_home_win_probability REAL,
    source TEXT NOT NULL DEFAULT 'pregame',
    features_json TEXT NOT NULL DEFAULT '{}',
    actual_home_score INTEGER,
    actual_away_score INTEGER,
    settled_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_predictions_game_date ON predictions(game_date);
CREATE INDEX IF NOT EXISTS idx_predictions_game_id ON predictions(game_id);
"""


@dataclass(frozen=True)
class PredictionRecord:
    prediction_id: str
    game_id: str
    game_date: str
    home_team: str
    away_team: str
    predicted_at: str
    model_name: str
    model_version: str
    predicted_total: float
    market_total_line: float | None = None
    predicted_margin: float | None = None
    home_handicap_line: float | None = None
    predicted_home_win_probability: float | None = None
    source: str = "pregame"
    features: dict[str, Any] | None = None


class PredictionTracker:
    """Almacén SQLite; las predicciones existentes no se sobrescriben."""

    def __init__(self, database: str | Path = "data/nba_totals_tracker.sqlite3"):
        self.database = str(database)
        if self.database != ":memory:":
            Path(self.database).expanduser().resolve().parent.mkdir(
                parents=True, exist_ok=True
            )
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def record_prediction(self, prediction: PredictionRecord) -> bool:
        """Inserta una predicción congelada. Devuelve False si ya existe."""
        if not prediction.prediction_id or not prediction.game_id:
            raise ValueError("prediction_id y game_id son obligatorios")
        if prediction.source != "pregame":
            raise ValueError("Fase 1 solo registra predicciones pregame")
        if prediction.predicted_total < 0:
            raise ValueError("predicted_total no puede ser negativo")
        if prediction.predicted_home_win_probability is not None and not (
            0 <= prediction.predicted_home_win_probability <= 1
        ):
            raise ValueError("La probabilidad debe estar entre 0 y 1")

        values = asdict(prediction)
        features = values.pop("features") or {}
        now = datetime.now(timezone.utc).isoformat()
        columns = list(values) + ["features_json", "created_at"]
        payload = list(values.values()) + [
            json.dumps(features, sort_keys=True),
            now,
        ]
        placeholders = ",".join("?" for _ in columns)
        with self._connect() as connection:
            cursor = connection.execute(
                f"INSERT OR IGNORE INTO predictions ({','.join(columns)}) "
                f"VALUES ({placeholders})",
                payload,
            )
            return cursor.rowcount == 1

    def settle_game(
        self, game_id: str, home_score: int, away_score: int
    ) -> int:
        """Asocia el resultado final a todas las predicciones del game_id."""
        if home_score < 0 or away_score < 0:
            raise ValueError("Los marcadores no pueden ser negativos")
        settled_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE predictions
                   SET actual_home_score = ?, actual_away_score = ?, settled_at = ?
                   WHERE game_id = ? AND settled_at IS NULL""",
                (home_score, away_score, settled_at, str(game_id)),
            )
            return cursor.rowcount

    def list_predictions(
        self, *, settled_only: bool = False, limit: int = 500
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10000:
            raise ValueError("limit debe estar entre 1 y 10000")
        where = "WHERE settled_at IS NOT NULL" if settled_only else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM predictions {where} "
                "ORDER BY game_date DESC, created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["features"] = json.loads(item.pop("features_json"))
            result.append(item)
        return result

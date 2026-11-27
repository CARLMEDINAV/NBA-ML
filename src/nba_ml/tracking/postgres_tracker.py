"""PostgreSQL/Supabase implementation of the prediction tracker."""
from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from psycopg import connect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .prediction_tracker import PredictionRecord


class PostgresPredictionTracker:
    """Persistent tracker backed by PostgreSQL (including Supabase)."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
        if not self.database_url:
            raise ValueError("Configura SUPABASE_DB_URL o DATABASE_URL")
        parsed = urlparse(self.database_url)
        if parsed.scheme not in {"postgres", "postgresql"}:
            raise ValueError("La URL debe usar el esquema postgres:// o postgresql://")

    def _connect(self):
        return connect(self.database_url, row_factory=dict_row, connect_timeout=10)

    def record_prediction(self, prediction: PredictionRecord) -> bool:
        if not prediction.prediction_id or not prediction.game_id:
            raise ValueError("prediction_id y game_id son obligatorios")
        if prediction.source != "pregame":
            raise ValueError("Fase 1 solo registra predicciones pregame")
        if prediction.predicted_total < 0:
            raise ValueError("predicted_total no puede ser negativo")
        probability = prediction.predicted_home_win_probability
        if probability is not None and not 0 <= probability <= 1:
            raise ValueError("La probabilidad debe estar entre 0 y 1")

        values = asdict(prediction)
        features = values.pop("features") or {}
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO nba_predictions (
                    prediction_id, game_id, game_date, home_team, away_team,
                    predicted_at, model_name, model_version, predicted_total,
                    market_total_line, predicted_margin, home_handicap_line,
                    predicted_home_win_probability, source, features, created_at
                ) VALUES (
                    %(prediction_id)s, %(game_id)s, %(game_date)s, %(home_team)s,
                    %(away_team)s, %(predicted_at)s, %(model_name)s, %(model_version)s,
                    %(predicted_total)s, %(market_total_line)s, %(predicted_margin)s,
                    %(home_handicap_line)s, %(predicted_home_win_probability)s,
                    %(source)s, %(features)s, %(created_at)s
                )
                ON CONFLICT (prediction_id) DO NOTHING
                """,
                {**values, "features": Jsonb(features), "created_at": now},
            )
            return cursor.rowcount == 1

    def settle_game(self, game_id: str, home_score: int, away_score: int) -> int:
        if home_score < 0 or away_score < 0:
            raise ValueError("Los marcadores no pueden ser negativos")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE nba_predictions
                SET actual_home_score = %s, actual_away_score = %s, settled_at = %s
                WHERE game_id = %s AND settled_at IS NULL
                """,
                (home_score, away_score, datetime.now(timezone.utc), str(game_id)),
            )
            return cursor.rowcount

    def list_predictions(self, *, settled_only: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10000:
            raise ValueError("limit debe estar entre 1 y 10000")
        where = "WHERE settled_at IS NOT NULL" if settled_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT prediction_id, game_id, game_date, home_team, away_team,
                       predicted_at, model_name, model_version, predicted_total,
                       market_total_line, predicted_margin, home_handicap_line,
                       predicted_home_win_probability, source, features,
                       actual_home_score, actual_away_score, settled_at, created_at
                FROM nba_predictions {where}
                ORDER BY game_date DESC, created_at DESC LIMIT %s
                """,
                (limit,),
            ).fetchall()
        for row in rows:
            row["features"] = row.pop("features") or {}
        return rows

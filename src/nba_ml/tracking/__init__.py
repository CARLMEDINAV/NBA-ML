"""Persistencia de predicciones y resultados de NBA TOTALS."""

import os

from .prediction_tracker import PredictionRecord, PredictionTracker


def create_prediction_tracker(database: str | None = None):
    """Selecciona PostgreSQL/Supabase en producción y SQLite localmente."""
    database_url = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
    if database_url:
        from .postgres_tracker import PostgresPredictionTracker
        return PostgresPredictionTracker(database_url)
    return PredictionTracker(database or os.getenv("NBA_TRACKER_SQLITE_PATH", "data/nba_totals_tracker.sqlite3"))


__all__ = [
    "PredictionRecord",
    "PredictionTracker",
    "create_prediction_tracker",
]

from nba_ml.tracking import PredictionTracker, create_prediction_tracker


def test_factory_uses_sqlite_without_database_url(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    tracker = create_prediction_tracker(str(tmp_path / "tracker.sqlite3"))
    assert isinstance(tracker, PredictionTracker)


def test_factory_selects_supabase_postgres(monkeypatch):
    monkeypatch.setenv(
        "SUPABASE_DB_URL",
        "postgresql://postgres:secret@example.supabase.co:5432/postgres",
    )
    tracker = create_prediction_tracker()
    assert tracker.__class__.__name__ == "PostgresPredictionTracker"


def test_postgres_tracker_rejects_non_postgres_url():
    from nba_ml.tracking.postgres_tracker import PostgresPredictionTracker

    try:
        PostgresPredictionTracker("https://example.supabase.co")
    except ValueError as error:
        assert "postgres://" in str(error)
    else:
        raise AssertionError("Expected invalid database URL to be rejected")

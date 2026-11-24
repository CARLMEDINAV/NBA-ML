from nba_ml.tracking import PredictionRecord, PredictionTracker


def sample(**overrides):
    values = dict(
        prediction_id="pred-1",
        game_id="game-1",
        game_date="2026-11-23",
        home_team="HOU",
        away_team="LAC",
        predicted_at="2026-11-23T18:00:00-05:00",
        model_name="ridge",
        model_version="totals-v1",
        predicted_total=228.1,
        market_total_line=225.5,
        predicted_margin=11.0,
        home_handicap_line=-11.0,
        predicted_home_win_probability=0.72,
        features={"pace_sum": 198.2},
    )
    values.update(overrides)
    return PredictionRecord(**values)


def test_record_is_idempotent_and_immutable(tmp_path):
    tracker = PredictionTracker(tmp_path / "tracker.sqlite")
    assert tracker.record_prediction(sample()) is True
    assert tracker.record_prediction(sample(predicted_total=190.0)) is False
    rows = tracker.list_predictions()
    assert len(rows) == 1
    assert rows[0]["predicted_total"] == 228.1
    assert rows[0]["features"] == {"pace_sum": 198.2}


def test_settle_once_and_persist_after_reopen(tmp_path):
    path = tmp_path / "tracker.sqlite"
    tracker = PredictionTracker(path)
    tracker.record_prediction(sample())
    assert tracker.settle_game("game-1", 116, 104) == 1
    assert tracker.settle_game("game-1", 100, 100) == 0
    reopened = PredictionTracker(path)
    row = reopened.list_predictions(settled_only=True)[0]
    assert row["actual_home_score"] == 116
    assert row["actual_away_score"] == 104
    assert row["settled_at"]


def test_rejects_live_source_and_invalid_probability(tmp_path):
    tracker = PredictionTracker(tmp_path / "tracker.sqlite")
    import pytest

    with pytest.raises(ValueError):
        tracker.record_prediction(sample(source="live"))
    with pytest.raises(ValueError):
        tracker.record_prediction(sample(predicted_home_win_probability=1.5))

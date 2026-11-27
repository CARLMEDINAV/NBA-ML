from nba_ml.evaluation import evaluate_predictions


def test_historical_metrics_and_market_push_handling():
    rows = [
        {
            "predicted_total": 228.0,
            "market_total_line": 225.5,
            "predicted_margin": 8.0,
            "home_handicap_line": -4.5,
            "predicted_home_win_probability": 0.75,
            "actual_home_score": 116,
            "actual_away_score": 104,
        },
        {
            "predicted_total": 220.0,
            "market_total_line": 225.5,
            "predicted_margin": -2.0,
            "home_handicap_line": 1.5,
            "predicted_home_win_probability": 0.40,
            "actual_home_score": 111,
            "actual_away_score": 117,
        },
        {
            "predicted_total": 230.0,
            "market_total_line": 230.0,
            "predicted_margin": 1.0,
            "home_handicap_line": 0.0,
            "predicted_home_win_probability": 0.51,
            "actual_home_score": 110,
            "actual_away_score": 120,
        },
    ]
    result = evaluate_predictions(rows)
    assert result["n_settled"] == 3
    assert round(result["mae"], 4) == round(16 / 3, 4)
    assert round(result["rmse"], 4) == round((128 / 3) ** 0.5, 4)
    assert result["bias"] == 0.0
    assert result["total_pick_count"] == 2
    assert result["total_hit_rate"] == 0.5
    assert round(result["winner_hit_rate"], 4) == round(2 / 3, 4)
    assert result["spread_pick_count"] == 3
    assert round(result["spread_hit_rate"], 4) == round(2 / 3, 4)


def test_unsettled_rows_are_excluded():
    result = evaluate_predictions([{"predicted_total": 200}])
    assert result["n_settled"] == 0
    assert result["mae"] is None
    assert result["total_hit_rate"] is None

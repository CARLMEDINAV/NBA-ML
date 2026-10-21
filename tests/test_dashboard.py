"""Test de humo del dashboard: arranca y produce una prediccion."""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

from nba_ml.config import MODELS_DIR, PROCESSED_DIR, PROJECT_ROOT
from nba_ml.data.schedule import current_season, schedule_path

DASHBOARD = PROJECT_ROOT / "src" / "nba_ml" / "dashboard.py"


@pytest.mark.skipif(
    not (MODELS_DIR / "model.joblib").exists()
    or not (PROCESSED_DIR / "team_state.csv").exists(),
    reason="Requiere modelo entrenado y estado de equipos (nba-ml all)",
)
def test_dashboard_runs_and_predicts():
    app = AppTest.from_file(str(DASHBOARD), default_timeout=180).run()

    assert not app.exception
    assert not app.error

    labels = [metric.label for metric in app.metric]
    assert "Total previsto" in labels
    assert any(label.startswith("P(over") for label in labels)

    values = {metric.label: metric.value for metric in app.metric}
    predicted = float(values["Total previsto"].split()[0])
    assert 150.0 < predicted < 320.0

    assert "Próximos partidos" in [tab.label for tab in app.tabs]

    if schedule_path(current_season()).exists():
        upcoming = app.dataframe[0].value
        assert not upcoming.empty
        assert {"Fecha", "Partido", "Total", "Inclinación"} <= set(upcoming.columns)

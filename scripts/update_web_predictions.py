"""Guarda snapshots de predicciones antes del inicio de cada partido.

La sección Resultados no debe reconstruir predicciones con el estado actual:
eso puede usar estadisticas posteriores al partido. Este job congela la
prediccion dentro de las 3 horas previas al inicio y nunca la sobrescribe.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
SCHEDULE = ARTIFACTS / "schedule_2026_27.csv"
STATE = ARTIFACTS / "team_state.csv"
MODEL = ARTIFACTS / "model.joblib"
OUTPUT = ARTIFACTS / "pregame_predictions_2026_27.csv"
EASTERN = ZoneInfo("America/New_York")
WINDOW = timedelta(hours=3)
COLUMNS = [
    "game_id", "game_date", "home_team", "away_team",
    "predicted_total", "predicted_margin", "prediction_created_at",
]


def _start_time(row: pd.Series) -> datetime | None:
    try:
        local = datetime.strptime(
            f"{pd.Timestamp(row['game_date']).date()} {str(row['game_time_et'])[:5]}",
            "%Y-%m-%d %H:%M",
        )
        return local.replace(tzinfo=EASTERN)
    except (KeyError, TypeError, ValueError):
        return None


def update_predictions(now: datetime | None = None) -> pd.DataFrame:
    # Imports internos para que el script tambien funcione desde GitHub Actions.
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from nba_ml.models.predict import load_artifact, load_team_state, predict_game

    now = now or datetime.now(EASTERN)
    if now.tzinfo is None:
        raise ValueError("now debe incluir zona horaria")

    schedule = pd.read_csv(SCHEDULE, dtype={"game_id": str}, parse_dates=["game_date"])
    if OUTPUT.exists():
        saved = pd.read_csv(OUTPUT, dtype={"game_id": str})
    else:
        saved = pd.DataFrame(columns=COLUMNS)
    known = set(saved.get("game_id", pd.Series(dtype=str)).astype(str))

    pending = schedule[
        pd.to_numeric(schedule["status"], errors="coerce").eq(1)
    ].copy()
    if pending.empty:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        saved.reindex(columns=COLUMNS).to_csv(OUTPUT, index=False)
        return saved.reindex(columns=COLUMNS)

    artifact = load_artifact(MODEL)
    state = load_team_state(STATE)
    rows = []
    for _, game in pending.iterrows():
        game_id = str(game["game_id"])
        if game_id in known:
            continue
        start = _start_time(game)
        if start is None or not (now < start <= now + WINDOW):
            continue
        try:
            result = predict_game(
                artifact,
                state,
                home=str(game["home"]),
                away=str(game["away"]),
                as_of=pd.Timestamp(game["game_date"]),
                warn_stale=False,
            )
        except (ValueError, KeyError):
            continue
        rows.append({
            "game_id": game_id,
            "game_date": pd.Timestamp(game["game_date"]).strftime("%Y-%m-%d"),
            "home_team": str(game["home"]),
            "away_team": str(game["away"]),
            "predicted_total": float(result["predicted_total"]),
            "predicted_margin": (
                float(result["predicted_margin"])
                if result.get("predicted_margin") is not None else float("nan")
            ),
            "prediction_created_at": now.isoformat(),
        })

    if rows:
        saved = pd.concat([saved, pd.DataFrame(rows)], ignore_index=True)
    saved = saved.reindex(columns=COLUMNS).drop_duplicates("game_id", keep="first")
    saved = saved.sort_values(["game_date", "game_id"]).reset_index(drop=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    saved.to_csv(OUTPUT, index=False)
    print(f"{OUTPUT.relative_to(ROOT)}: {len(rows)} nuevas predicciones prepartido; {len(saved)} acumuladas")
    return saved


if __name__ == "__main__":
    update_predictions()

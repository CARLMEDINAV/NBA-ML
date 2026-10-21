"""Dashboard interactivo del modelo de totales NBA (Streamlit).

Ejecutar con:
    nba-ml dashboard
    streamlit run src/nba_ml/dashboard.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from nba_ml.config import FIGURES_DIR, PROCESSED_DIR, REPORTS_DIR
from nba_ml.models.predict import load_artifact, load_team_state, predict_game

MODEL_FIGURES = (
    "pred_vs_actual.png",
    "residuals.png",
    "walk_forward_mae.png",
    "feature_importance.png",
)
DATA_FIGURES = ("totals_by_season.png", "scoring_trend.png", "feature_correlations.png")


@st.cache_resource(show_spinner=False)
def _artifact() -> dict:
    return load_artifact()


@st.cache_data(show_spinner=False, ttl=300)
def _state() -> pd.DataFrame:
    return load_team_state()


@st.cache_data(show_spinner=False, ttl=300)
def _team_games() -> pd.DataFrame:
    path = PROCESSED_DIR / "team_games.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"game_id": str}, parse_dates=["game_date"])


@st.cache_data(show_spinner=False, ttl=300)
def _games() -> pd.DataFrame:
    path = PROCESSED_DIR / "games.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"game_id": str}, parse_dates=["game_date"])


def _read_report(name: str) -> str | None:
    path = REPORTS_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else None


def _format(value: float | None, decimals: int = 1) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:.{decimals}f}"


def _render_prediction(result: dict, state: pd.DataFrame, home: str, away: str) -> None:
    home_row = state[state["team_abbreviation"] == home].iloc[0]
    away_row = state[state["team_abbreviation"] == away].iloc[0]

    st.subheader(f"{result['home']} vs {result['away']}")
    st.caption(
        f"{result['as_of']} · local {result['home']} · visitante {result['away']}"
    )

    low, high = result["interval_80"]
    probability = result["probability_over"]
    line = result["line"]

    metric_columns = st.columns(4)
    metric_columns[0].metric("Total previsto", f"{result['predicted_total']:.1f} pts")
    metric_columns[1].metric("Intervalo 80%", f"{low:.0f} – {high:.0f}")
    metric_columns[2].metric(f"P(over {line:.1f})", f"{probability:.1%}")
    metric_columns[3].metric(
        "Sesgo vs 50%", f"{(probability - 0.5) * 100:+.1f} pp"
    )

    st.progress(
        min(max(probability, 0.0), 1.0),
        text=f"OVER {probability:.0%}  ·  UNDER {1 - probability:.0%}",
    )

    if probability >= 0.55:
        st.success(
            "El modelo se inclina por el OVER, pero la ventaja es moderada. "
            "No es una recomendación de apuestas."
        )
    elif probability <= 0.45:
        st.error(
            "El modelo se inclina por el UNDER, pero la ventaja es moderada. "
            "No es una recomendación de apuestas."
        )
    else:
        st.info(
            "Probabilidad prácticamente 50/50: el modelo no ve una ventaja clara "
            "para esta línea."
        )

    st.divider()
    comparison = pd.DataFrame(
        {
            "Métrica": [
                "Elo",
                "Puntos últimos 10",
                "Puntos recibidos (10)",
                "Pace (posesiones, 10)",
                "Descanso (días)",
                "Back-to-back",
                "Último partido",
            ],
            home: [
                _format(result.get("home_elo")),
                _format(home_row.get("pts_roll10")),
                _format(home_row.get("opp_pts_roll10")),
                _format(home_row.get("pace_roll10")),
                _format(result.get("home_rest"), 0),
                "Sí" if float(result.get("home_rest", 3)) <= 1 else "No",
                str(result.get("home_last_game")),
            ],
            away: [
                _format(result.get("away_elo")),
                _format(away_row.get("pts_roll10")),
                _format(away_row.get("opp_pts_roll10")),
                _format(away_row.get("pace_roll10")),
                _format(result.get("away_rest"), 0),
                "Sí" if float(result.get("away_rest", 3)) <= 1 else "No",
                str(result.get("away_last_game")),
            ],
        }
    ).set_index("Métrica")
    st.dataframe(comparison, height=300)
    st.caption(
        "Elo mostrado tras el último partido (con regresión entre temporadas si "
        "el partido es de una temporada nueva)."
    )

    team_games = _team_games()
    if not team_games.empty:
        recent = (
            team_games[team_games["team_abbreviation"].isin([home, away])]
            .sort_values(["team_abbreviation", "game_date"])
            .groupby("team_abbreviation")
            .tail(10)
        )
        pivot = recent.pivot_table(
            index="game_date", columns="team_abbreviation", values="game_total"
        )
        st.subheader("Totales de los últimos 10 partidos")
        st.line_chart(pivot, height=300)
        st.caption(
            "Cada punto es el total de puntos anotados por ambos equipos en un "
            "partido del equipo. La línea horizontal mental es la línea de apuestas."
        )

    st.warning(
        "El modelo no usa lesiones, alineaciones ni cuotas. La probabilidad es una "
        "estimación del modelo, no una ventaja real de mercado.",
        icon="⚠️",
    )


def _render_performance() -> None:
    report = _read_report("metrics.md")
    if report:
        st.markdown(report)
    else:
        st.info("Ejecuta `nba-ml train` para generar `reports/metrics.md`.")

    existing = [name for name in MODEL_FIGURES if (FIGURES_DIR / name).exists()]
    columns = st.columns(2)
    for index, name in enumerate(existing):
        columns[index % 2].image(str(FIGURES_DIR / name))


def _render_data() -> None:
    games = _games()
    if games.empty:
        st.info("Ejecuta `nba-ml build` para generar `data/processed/games.csv`.")
        return

    st.subheader("Temporadas")
    summary = (
        games.groupby("season")
        .agg(
            partidos=("game_id", "count"),
            media=("total_points", "mean"),
            mediana=("total_points", "median"),
            desv_tipica=("total_points", "std"),
            prorroga_pct=("is_overtime", "mean"),
        )
        .reset_index()
        .round(2)
    )
    st.dataframe(summary, hide_index=True)

    st.subheader("Contexto histórico")
    columns = st.columns(2)
    for index, name in enumerate(DATA_FIGURES):
        path = FIGURES_DIR / name
        if path.exists():
            columns[index % 2].image(str(path))

    with st.expander("Informe de EDA completo"):
        report = _read_report("eda.md")
        st.markdown(report or "Ejecuta `nba-ml eda` para generarlo.")


def _sidebar(state: pd.DataFrame, artifact: dict) -> tuple[str, str, dt.date, float]:
    teams = sorted(state["team_abbreviation"].dropna().unique())
    default_home = "BOS" if "BOS" in teams else teams[0]
    default_away = "LAL" if "LAL" in teams and default_home != "LAL" else teams[1]

    with st.sidebar:
        st.header("Partido")
        home = st.selectbox("Equipo local", teams, index=teams.index(default_home))
        away = st.selectbox(
            "Equipo visitante",
            teams,
            index=teams.index(default_away),
        )

        last_game = pd.to_datetime(state["last_game_date"]).max().date()
        minimum = last_game + dt.timedelta(days=1)
        default_date = max(dt.date.today(), minimum)
        match_date = st.date_input(
            "Fecha del partido",
            value=default_date,
            min_value=minimum,
        )

        line = st.number_input(
            "Línea over/under",
            min_value=150.0,
            max_value=320.0,
            value=225.5,
            step=0.5,
        )

        st.divider()
        st.caption(
            f"Modelo **{artifact['model_name']}** · {len(artifact['features'])} features\n\n"
            f"Datos hasta **{artifact['trained_through']}** · sigma {artifact['residual_sigma']:.1f}"
        )

        if st.button("Recargar datos y modelo"):
            st.cache_data.clear()
            st.cache_resource.clear()
            st.rerun()

        st.divider()
        st.caption(
            "Proyecto educativo. No usa lesiones, alineaciones ni cuotas. "
            "No es consejo de apuestas."
        )

    return home, away, match_date, float(line)


def main() -> None:
    st.set_page_config(page_title="NBA Totals ML", page_icon="🏀", layout="wide")

    st.title("🏀 NBA Totals ML")
    st.caption(
        "Predicción del total de puntos (over/under) con datos de stats.nba.com. "
        "Modelo entrenado con 11 temporadas regulares (2015-16 a 2025-26)."
    )

    try:
        artifact = _artifact()
        state = _state()
    except FileNotFoundError as error:
        st.error(f"{error}")
        st.code("nba-ml all", language="powershell")
        st.stop()

    home, away, match_date, line = _sidebar(state, artifact)

    if home == away:
        st.warning("Elige dos equipos distintos.")
        st.stop()

    try:
        result = predict_game(
            artifact,
            state,
            home=home,
            away=away,
            as_of=pd.Timestamp(match_date),
            line=line,
        )
    except ValueError as error:
        st.error(str(error))
        st.stop()

    oldest_last_game = min(
        pd.Timestamp(result["home_last_game"]), pd.Timestamp(result["away_last_game"])
    )
    gap = (pd.Timestamp(match_date) - oldest_last_game).days
    if gap > 14:
        st.warning(
            f"El último partido de uno de los equipos fue hace {gap} días. "
            "Los datos están desactualizados y la predicción asume descanso máximo. "
            "Actualiza la temporada antes de usarla en partidos reales.",
            icon="📅",
        )

    prediction_tab, performance_tab, data_tab = st.tabs(
        ["Predicción", "Rendimiento del modelo", "Datos"]
    )
    with prediction_tab:
        _render_prediction(result, state, home, away)
    with performance_tab:
        _render_performance()
    with data_tab:
        _render_data()


def run(port: int = 8501) -> None:
    """Lanza Streamlit desde la CLI del proyecto."""
    from streamlit.web import cli as streamlit_cli

    sys.argv = [
        "streamlit",
        "run",
        str(Path(__file__).resolve()),
        "--server.port",
        str(port),
    ]
    raise SystemExit(streamlit_cli.main())


if __name__ == "__main__":
    main()

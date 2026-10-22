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

import altair as alt

from nba_ml.config import FIGURES_DIR, PROCESSED_DIR, REPORTS_DIR, SEASONS
from nba_ml.data.players import load_player_stats, player_stats_path
from nba_ml.data.schedule import (
    current_season,
    load_schedule,
    schedule_path,
    upcoming_games,
)
from nba_ml.models.predict import load_artifact, load_team_state, predict_game
from nba_ml.models.results import (
    add_over_under,
    load_test_predictions,
    summarize_results,
)

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


def _render_upcoming(artifact: dict, state: pd.DataFrame, line: float) -> None:
    season = current_season()
    st.subheader(f"Próximos partidos · {season}")

    if not schedule_path(season).exists():
        st.info(
            "Todavía no hay calendario descargado para esta temporada. "
            "Se guarda en `data/raw/` y luego se actualiza cuando quieras."
        )
        if st.button("Descargar calendario", type="primary"):
            with st.spinner("Descargando calendario desde stats.nba.com..."):
                try:
                    load_schedule(season, refresh=True)
                except Exception as error:  # red, timeout o cambio de API
                    st.error(f"No se pudo descargar el calendario: {error}")
                else:
                    st.rerun()
        return

    schedule = load_schedule(season)
    teams = sorted(set(schedule["home"]) | set(schedule["away"]))

    controls = st.columns([2, 1, 2])
    days = controls[0].slider(
        "Días a mostrar", min_value=1, max_value=30, value=7, key="upcoming_days"
    )
    if controls[1].button(
        "Actualizar",
        help="Vuelve a descargar el calendario",
        key="upcoming_refresh",
    ):
        with st.spinner("Actualizando calendario..."):
            try:
                load_schedule(season, refresh=True)
            except Exception as error:
                st.error(f"No se pudo actualizar el calendario: {error}")
            else:
                st.rerun()
    selected_teams = controls[2].multiselect(
        "Filtrar por equipo", teams, default=[], key="upcoming_teams"
    )

    start = pd.Timestamp.today().normalize()
    end = start + pd.Timedelta(days=int(days) - 1)
    games = upcoming_games(
        schedule,
        start=start,
        end=end,
        only_scheduled=True,
        teams=selected_teams or None,
    )

    last_state = pd.to_datetime(state["last_game_date"]).max().normalize()
    stale_days = (pd.Timestamp.today().normalize() - last_state).days
    if stale_days > 7:
        st.warning(
            f"El estado de los equipos es del {last_state.date()} "
            f"({stale_days} días). Las predicciones usan la última temporada "
            "completa; actualiza los datos para incorporar los partidos de esta.",
            icon="📅",
        )

    if games.empty:
        st.info(
            "No hay partidos programados en el rango elegido"
            + (" para esos equipos." if selected_teams else ".")
        )
        future = schedule[schedule["status"].eq(1)].copy()
        future["game_date"] = pd.to_datetime(future["game_date"])
        future = future[future["game_date"] >= start]
        if not future.empty:
            st.caption(f"El próximo partido es el {future['game_date'].min().date()}.")
        return

    weekdays = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
    rows: list[dict[str, object]] = []
    skipped = 0
    for game in games.itertuples(index=False):
        try:
            result = predict_game(
                artifact,
                state,
                home=game.home,
                away=game.away,
                as_of=pd.Timestamp(game.game_date),
                line=line,
                warn_stale=False,
            )
        except ValueError:
            skipped += 1
            continue

        low, high = result["interval_80"]
        probability = float(result["probability_over"])
        if probability >= 0.55:
            signal = "OVER"
        elif probability <= 0.45:
            signal = "UNDER"
        else:
            signal = "—"

        game_date = pd.Timestamp(game.game_date)
        time_et = "—" if pd.isna(game.game_time_et) else str(game.game_time_et)
        rows.append(
            {
                "Fecha": f"{weekdays[game_date.weekday()]} {game_date:%d/%m}",
                "Hora ET": time_et,
                "Partido": f"{game.away} @ {game.home}",
                "Total": round(float(result["predicted_total"]), 1),
                "Pts local": (
                    round(float(result["projected_home_score"]), 1)
                    if result.get("projected_home_score") is not None
                    else "—"
                ),
                "Pts visitante": (
                    round(float(result["projected_away_score"]), 1)
                    if result.get("projected_away_score") is not None
                    else "—"
                ),
                "IC 80%": f"{low:.0f} – {high:.0f}",
                f"P(over {line:.1f})": f"{probability:.1%}",
                "Inclinación": signal,
                "Ganador probable": result.get("possible_winner") or "—",
                "Hándicap modelo": result.get("model_handicap") or "—",
            }
        )

    if not rows:
        st.warning("No se pudo predecir ninguno de los partidos del rango.")
        return

    table = pd.DataFrame(rows)
    st.dataframe(
        table,
        hide_index=True,
        height=min(620, 40 + 35 * len(table)),
        column_config={
            "Total": st.column_config.NumberColumn("Total previsto", format="%.1f"),
            "Pts local": st.column_config.NumberColumn("Pts local", format="%.1f"),
            "Pts visitante": st.column_config.NumberColumn(
                "Pts visitante", format="%.1f"
            ),
        },
    )
    st.caption(
        f"{len(table)} partidos · línea de referencia {line:.1f} · "
        f"probabilidad estimada con el estado al {last_state.date()}"
        + (f" · {skipped} partidos omitidos" if skipped else "")
    )

    with st.expander("Cómo leer esta tabla"):
        st.markdown(
            "El total previsto es la salida del modelo Ridge. **P(over)** es la "
            "probabilidad empírica de superar la línea de referencia de la barra "
            "lateral, calculada con los residuos fuera de muestra. La señal solo "
            "aparece cuando la probabilidad se aleja del 50% (≥55% OVER, ≤45% "
            "UNDER); en caso contrario el modelo no ve ventaja.\n\n"
            "**Pts local/visitante**, **ganador probable** y **hándicap modelo** "
            "salen de un segundo Ridge sobre el margen. El hándicap se redondea "
            "a 0.5 y es una estimación, no una línea oficial.\n\n"
            "El modelo no conoce lesiones, alineaciones, cuotas ni el resultado "
            "de los partidos que se juegan hoy. No es consejo de apuestas."
        )


def _render_results(artifact: dict, state: pd.DataFrame, line: float) -> None:
    st.subheader("Resultados anteriores")
    st.caption(
        "Partidos ya jugados con total real, predicción y acierto over/under. "
        "La línea es la de referencia de la barra lateral."
    )

    seasons = list(dict.fromkeys([SEASONS[-1], current_season()]))
    default_index = 0
    if len(seasons) > 1 and schedule_path(current_season()).exists():
        cached = load_schedule(current_season())
        if (
            "total_points" in cached.columns
            and cached["status"].eq(3).any()
            and cached["total_points"].notna().any()
        ):
            default_index = seasons.index(current_season())

    season = st.selectbox(
        "Temporada de resultados",
        seasons,
        index=default_index,
        key="results_season",
        format_func=lambda value: (
            f"{value} ({'en curso' if value == current_season() else 'completa'})"
        ),
    )

    if season == SEASONS[-1]:
        try:
            frame = load_test_predictions()
        except FileNotFoundError as error:
            st.info(str(error))
            return
        frame = add_over_under(frame, line, artifact["residuals"])
        source = "Predicciones fuera de muestra de la temporada de test"
    else:
        if not schedule_path(season).exists():
            st.info("Descarga el calendario en la pestaña Próximos partidos.")
            return
        schedule = load_schedule(season)
        if "total_points" not in schedule.columns:
            schedule = load_schedule(season, refresh=True)
        finals = schedule[
            schedule["status"].eq(3) & schedule["total_points"].notna()
        ].copy()
        if finals.empty:
            st.info(f"Todavía no hay partidos finalizados en {season}.")
            return

        rows: list[dict[str, object]] = []
        for game in finals.itertuples(index=False):
            try:
                result = predict_game(
                    artifact,
                    state,
                    home=game.home,
                    away=game.away,
                    as_of=pd.Timestamp(game.game_date),
                    line=line,
                    warn_stale=False,
                )
            except ValueError:
                continue
            rows.append(
                {
                    "game_date": pd.Timestamp(game.game_date),
                    "home_team": game.home,
                    "away_team": game.away,
                    "total_points": float(game.total_points),
                    "predicted_total": float(result["predicted_total"]),
                }
            )
        if not rows:
            st.warning("No se pudo predecir ninguno de los partidos finalizados.")
            return
        frame = add_over_under(pd.DataFrame(rows), line, artifact["residuals"])
        source = "Partidos finalizados de la temporada en curso"

    controls = st.columns([1.6, 1.2])
    teams = sorted(set(frame["home_team"]) | set(frame["away_team"]))
    selected_teams = controls[0].multiselect(
        "Equipo", teams, default=[], key="results_teams"
    )
    rows_to_show = int(
        controls[1].selectbox(
            "Partidos a mostrar",
            [10, 25, 50, 100],
            index=1,
            key="results_rows",
        )
    )

    view = frame.copy()
    if selected_teams:
        view = view[
            view["home_team"].isin(selected_teams)
            | view["away_team"].isin(selected_teams)
        ]
    if view.empty:
        st.info("No hay partidos para ese filtro.")
        return

    summary = summarize_results(view)
    if summary["n"] == 0:
        st.info("No hay partidos con resultado y predicción para evaluar.")
        return
    st.caption(f"{source} · línea de referencia {line:.1f}")

    metrics = st.columns(5)
    metrics[0].metric("Partidos", summary["n"])
    metrics[1].metric(
        "MAE", f"{summary['mae']:.2f}" if pd.notna(summary["mae"]) else "—"
    )
    metrics[2].metric(
        "Acierto del modelo",
        f"{summary['hit_rate']:.1%}" if pd.notna(summary["hit_rate"]) else "—",
    )
    metrics[3].metric(
        "Over real",
        f"{summary['over_rate']:.1%}" if pd.notna(summary["over_rate"]) else "—",
    )
    metrics[4].metric(
        "Brier",
        f"{summary['brier']:.3f}" if pd.notna(summary["brier"]) else "—",
    )
    bias_text = f"{summary['bias']:+.2f}" if pd.notna(summary["bias"]) else "—"
    st.caption(
        f"{summary['n_leans']} partidos con inclinación clara (P(over) ≥55% o "
        f"≤45%) · sesgo medio {bias_text} puntos "
        "(positivo = el modelo sobreestima)"
    )
    st.caption(
        "Referencia sin modelo: elegir siempre OVER acertaría "
        f"{summary['over_rate']:.1%}; siempre UNDER, {1 - summary['over_rate']:.1%}."
    )

    display = view.sort_values("game_date", ascending=False).head(rows_to_show).copy()
    display["Fecha"] = pd.to_datetime(display["game_date"]).dt.strftime("%d/%m")
    display["Partido"] = display["away_team"] + " @ " + display["home_team"]
    display["Total real"] = display["total_points"].round(0).astype("Int64")
    display["Predicción"] = display["predicted_total"].round(1)
    display["Error"] = (display["predicted_total"] - display["total_points"]).round(1)
    display["Línea"] = display["line"].round(1)
    display["P(over)"] = display["probability_over"].map(
        lambda value: f"{value:.1%}" if pd.notna(value) else "—"
    )
    display["Resultado"] = display["actual_result"]
    display["Modelo"] = display["model_lean"]
    display["Acierto"] = display["hit"].map(
        lambda value: "✓" if value == 1 else ("✗" if value == 0 else "—")
    )
    table = display[
        [
            "Fecha",
            "Partido",
            "Total real",
            "Predicción",
            "Error",
            "Línea",
            "P(over)",
            "Resultado",
            "Modelo",
            "Acierto",
        ]
    ]
    st.dataframe(table, hide_index=True, height=min(650, 40 + 35 * len(table)))

    chart_data = view.dropna(subset=["total_points", "predicted_total"]).copy()
    if not chart_data.empty:
        chart_data["Acierto"] = chart_data["hit"].map(
            lambda value: "✓" if value == 1 else ("✗" if value == 0 else "—")
        )
        low = (
            min(
                chart_data["total_points"].min(),
                chart_data["predicted_total"].min(),
            )
            - 5
        )
        high = (
            max(
                chart_data["total_points"].max(),
                chart_data["predicted_total"].max(),
            )
            + 5
        )
        scatter = (
            alt.Chart(chart_data)
            .mark_circle(size=70, opacity=0.65)
            .encode(
                x=alt.X(
                    "predicted_total:Q",
                    title="Total previsto",
                    scale=alt.Scale(domain=[low, high]),
                ),
                y=alt.Y(
                    "total_points:Q",
                    title="Total real",
                    scale=alt.Scale(domain=[low, high]),
                ),
                color=alt.Color(
                    "Acierto:N",
                    scale=alt.Scale(
                        domain=["✓", "✗", "—"],
                        range=["#2ca02c", "#d62728", "#9e9e9e"],
                    ),
                    title=None,
                ),
                tooltip=[
                    alt.Tooltip("game_date:T", title="Fecha", format="%d/%m/%Y"),
                    "away_team:N",
                    "home_team:N",
                    alt.Tooltip("predicted_total:Q", title="Previsto", format=".1f"),
                    alt.Tooltip("total_points:Q", title="Real", format=".0f"),
                    alt.Tooltip("probability_over:Q", title="P(over)", format=".1%"),
                    "model_lean:N",
                    "actual_result:N",
                ],
            )
        )
        diagonal = (
            alt.Chart(pd.DataFrame({"x": [low, high], "y": [low, high]}))
            .mark_line(strokeDash=[5, 5], color="#888")
            .encode(x="x:Q", y="y:Q")
        )
        st.altair_chart(scatter + diagonal, width="stretch")
        st.caption(
            "Cada punto es un partido; la diagonal marca predicción = total real."
        )

    st.warning(
        "nba_api no proporciona líneas históricas de casas de apuestas. La línea "
        "usada es la de referencia de la barra lateral, no la línea de cierre. "
        "Para evaluar contra la línea real, habría que registrarla al predecir.",
        icon="⚠️",
    )


def _render_players() -> None:
    st.subheader("Estadísticas de jugadores")
    st.caption(
        "PRA = puntos + rebotes + asistencias. Fuente: `LeagueDashPlayerStats` "
        "de stats.nba.com."
    )

    seasons = list(dict.fromkeys([SEASONS[-1], current_season()]))
    controls = st.columns([1.2, 1.4, 1.2])
    season = controls[0].selectbox(
        "Temporada",
        seasons,
        index=0,
        key="players_season",
        format_func=lambda value: (
            f"{value} ({'en curso' if value == current_season() else 'completa'})"
        ),
    )
    mode_label = controls[1].radio(
        "Modo", ["Por partido", "Totales"], horizontal=True, key="players_mode"
    )
    per_mode = "PerGame" if mode_label == "Por partido" else "Totals"
    path = player_stats_path(season, per_mode)

    if controls[2].button(
        "Actualizar",
        help="Vuelve a descargar estas estadísticas",
        key="players_refresh",
    ):
        with st.spinner("Actualizando estadísticas de jugadores..."):
            try:
                load_player_stats(season, per_mode, refresh=True)
            except Exception as error:
                st.error(f"No se pudieron actualizar las estadísticas: {error}")
            else:
                st.rerun()

    if not path.exists():
        st.info(
            f"No hay estadísticas descargadas para {season} ({mode_label.lower()})."
        )
        if st.button("Descargar estadísticas", type="primary"):
            with st.spinner("Descargando desde stats.nba.com..."):
                try:
                    load_player_stats(season, per_mode, refresh=True)
                except Exception as error:
                    st.error(f"No se pudieron descargar las estadísticas: {error}")
                else:
                    st.rerun()
        return

    stats = load_player_stats(season, per_mode)
    if stats.empty:
        st.warning("La consulta no devolvió jugadores.")
        return

    max_gp = int(stats["gp"].max())
    if season == current_season() and max_gp <= 2:
        st.info(
            f"La temporada {season} acaba de empezar: hay {max_gp} partido(s) por "
            f"jugador. Para un ranking estable usa {SEASONS[-1]}."
        )

    filters = st.columns([1.6, 1.4, 1.2, 0.8])
    selected_teams = filters[0].multiselect(
        "Equipos", sorted(stats["team"].unique()), default=[], key="players_teams"
    )
    query = filters[1].text_input("Buscar jugador", placeholder="Nombre")
    sort_label = filters[2].selectbox("Ordenar por", ["PRA", "PTS", "REB", "AST"])
    rows = int(filters[3].selectbox("Filas", [10, 25, 50, 100], index=1))

    if max_gp > 1:
        min_gp = st.slider(
            "Mínimo de partidos jugados",
            1,
            max_gp,
            min(10, max_gp),
            key="players_min_gp",
        )
    else:
        min_gp = 1
        st.caption("Solo hay un partido por jugador: las medias son poco fiables.")

    view = stats[stats["gp"] >= min_gp]
    if selected_teams:
        view = view[view["team"].isin(selected_teams)]
    if query.strip():
        view = view[
            view["player_name"].str.contains(
                query.strip(), case=False, regex=False
            )
        ]
    if view.empty:
        st.info("No hay jugadores que cumplan esos filtros.")
        return

    sort_column = {"PRA": "pra", "PTS": "pts", "REB": "reb", "AST": "ast"}[
        sort_label
    ]
    view = view.sort_values([sort_column, "pra"], ascending=False)
    leader = view.iloc[0]

    summary = st.columns(3)
    summary[0].metric("Jugadores", len(view))
    summary[1].metric(f"Media de {sort_label}", f"{view[sort_column].mean():.1f}")
    summary[2].metric(
        f"Líder en {sort_label}",
        f"{leader['player_name']} · {leader[sort_column]:.1f}",
    )

    table = view.head(rows)[
        ["player_name", "team", "gp", "min", "pts", "reb", "ast", "pra"]
    ].copy()
    table.columns = ["Jugador", "Equipo", "Partidos", "Min", "PTS", "REB", "AST", "PRA"]
    for column in ["Min", "PTS", "REB", "AST", "PRA"]:
        table[column] = table[column].astype(float).round(1)

    st.dataframe(
        table,
        hide_index=True,
        height=min(700, 40 + 35 * len(table)),
    )
    st.caption(
        f"{len(view)} jugadores con al menos {min_gp} partidos · "
        f"modo {'por partido' if per_mode == 'PerGame' else 'totales'} · "
        "PRA = PTS + REB + AST"
    )

    st.subheader("Top 10 por PRA")
    chart_data = view.nlargest(10, "pra")[
        ["player_name", "team", "pts", "reb", "ast"]
    ].copy()
    chart_data["Jugador"] = (
        chart_data["player_name"] + " (" + chart_data["team"] + ")"
    )
    melted = chart_data.melt(
        id_vars="Jugador",
        value_vars=["pts", "reb", "ast"],
        var_name="Estadística",
        value_name="Valor",
    )
    melted["Estadística"] = melted["Estadística"].map(
        {"pts": "PTS", "reb": "REB", "ast": "AST"}
    )
    chart = (
        alt.Chart(melted)
        .mark_bar()
        .encode(
            y=alt.Y("Jugador:N", sort="-x", title=None),
            x=alt.X(
                "Valor:Q",
                title="Por partido" if per_mode == "PerGame" else "Total temporada",
            ),
            color=alt.Color(
                "Estadística:N",
                scale=alt.Scale(
                    domain=["PTS", "REB", "AST"],
                    range=["#4c78a8", "#f58518", "#54a24b"],
                ),
                title=None,
            ),
            tooltip=[
                "Jugador",
                "Estadística",
                alt.Tooltip("Valor:Q", format=".1f"),
            ],
        )
        .properties(height=max(280, 30 * len(chart_data)))
    )
    st.altair_chart(chart, width="stretch")


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

    if result.get("predicted_margin") is not None:
        margin = float(result["predicted_margin"])
        margin_columns = st.columns(4)
        margin_columns[0].metric("Margen previsto", f"{margin:+.1f} pts")
        margin_columns[1].metric(
            "Ganador probable", result.get("possible_winner") or "—"
        )
        margin_columns[2].metric(
            "Hándicap modelo", result.get("model_handicap") or "—"
        )
        margin_columns[3].metric(
            "P(gana local)",
            (
                f"{float(result['home_win_probability']):.1%}"
                if result.get("home_win_probability") is not None
                else "—"
            ),
        )
        if result.get("projected_home_score") is not None:
            st.caption(
                "Puntos proyectados: "
                f"{result['home']} {float(result['projected_home_score']):.1f} · "
                f"{result['away']} {float(result['projected_away_score']):.1f}"
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
        "El modelo no usa lesiones ni alineaciones. El hándicap y el ganador "
        "probable son estimaciones del modelo, no líneas oficiales ni una ventaja "
        "real de mercado.",
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
            "Línea de referencia (over/under)",
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

    (
        upcoming_tab,
        results_tab,
        players_tab,
        prediction_tab,
        performance_tab,
        data_tab,
    ) = st.tabs(
        [
            "Próximos partidos",
            "Resultados",
            "Jugadores",
            "Predicción",
            "Rendimiento del modelo",
            "Datos",
        ]
    )
    with upcoming_tab:
        _render_upcoming(artifact, state, line)
    with results_tab:
        _render_results(artifact, state, line)
    with players_tab:
        _render_players()
    with prediction_tab:
        _render_prediction(result, state, home, away)
    with performance_tab:
        _render_performance()
    with data_tab:
        _render_data()


def run(port: int = 8501, open_browser: bool = True) -> None:
    """Lanza Streamlit desde la CLI del proyecto.

    Se fuerza el modo headless para evitar el onboarding interactivo de
    Streamlit y se abre el navegador desde aqui.
    """
    import threading
    import webbrowser

    from streamlit.web import cli as streamlit_cli

    url = f"http://localhost:{port}"
    sys.argv = [
        "streamlit",
        "run",
        str(Path(__file__).resolve()),
        f"--server.port={port}",
        "--server.headless=true",
    ]
    if open_browser:
        threading.Timer(3.0, lambda: webbrowser.open(url)).start()
    raise SystemExit(streamlit_cli.main())


if __name__ == "__main__":
    main()

"""Dashboard NBA MODEL PRO: interfaz oscura y modular sobre el pipeline existente.

Ejecutar: nba-ml dashboard-pro
o: streamlit run src/nba_ml/dashboard_pro.py
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from nba_ml.config import SEASONS
from nba_ml.data.schedule import current_season, load_schedule, schedule_path, upcoming_games
from nba_ml.models.predict import load_artifact, load_team_state, predict_game
from nba_ml.dashboard import (
    _render_performance,
    _render_results,
    _render_players,
    _render_data,
)

CSS = """
<style>
:root { color-scheme: dark; }
.stApp { background: #050b1b; color: #e8eef9; }
[data-testid="stHeader"] { background: #0d1729; }
.block-container { max-width: 1500px; padding: 1.2rem 1.35rem 3rem; }
.pro-header { background:#0d1729; border:1px solid #1d2b42; border-radius:16px;
  padding:20px 24px; margin-bottom:18px; display:flex; align-items:center; gap:16px; }
.pro-logo { width:54px; height:54px; border-radius:16px; display:grid; place-items:center;
  background:#ff9d00; color:#07101f; font-size:22px; font-weight:900; }
.pro-title { font-size:25px; font-weight:850; color:#f7f9ff; line-height:1.1; }
.pro-sub { color:#8fa2bf; font-size:13px; margin-top:5px; }
.pro-version { background:#342817; color:#ffbd36; border:1px solid #73511a;
  border-radius:7px; padding:3px 7px; font-size:12px; margin-left:8px; }
.metric-card { background:#0d1729; border:1px solid #1e2d43; border-radius:15px;
  padding:16px 18px; min-height:108px; }
.metric-label { color:#91a6c4; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
.metric-value { color:#f5f8ff; font-size:27px; font-weight:800; margin-top:4px; }
.metric-note { color:#8da2c0; font-size:12px; margin-top:3px; }
.section-card { background:#0d1729; border:1px solid #1e2d43; border-radius:14px; padding:16px; }
div[data-testid="stTabs"] button { background:#0d1729; border:1px solid #1e2d43;
  border-radius:12px; margin-right:6px; color:#9bb0ce; font-weight:700; }
div[data-testid="stTabs"] button[aria-selected="true"] { background:#ff9d00; color:#07101f; }
div[data-testid="stSelectbox"] > div, div[data-testid="stMultiSelect"] > div,
div[data-testid="stNumberInput"] > div { background:#1a2940; border-radius:10px; }
[data-testid="stDataFrame"] { border:1px solid #1e2d43; border-radius:12px; overflow:hidden; }
.stCaption, [data-testid="stCaptionContainer"] { color:#8fa2bf; }
hr { border-color:#1e2d43; }
</style>
"""


def _metric(label: str, value: str, note: str) -> None:
    st.markdown(
        f'<div class="metric-card"><div class="metric-label">{label}</div>'
        f'<div class="metric-value">{value}</div><div class="metric-note">{note}</div></div>',
        unsafe_allow_html=True,
    )


@st.cache_resource(show_spinner=False)
def _artifact() -> dict:
    return load_artifact()


@st.cache_data(show_spinner=False, ttl=300)
def _state() -> pd.DataFrame:
    return load_team_state()


def _upcoming(artifact: dict, state: pd.DataFrame, days: int, teams: list[str]) -> None:
    season = current_season()
    if not schedule_path(season).exists():
        st.info("No hay calendario descargado para esta temporada.")
        if st.button("Descargar calendario", type="primary"):
            with st.spinner("Descargando calendario NBA..."):
                load_schedule(season, refresh=True)
            st.rerun()
        return

    schedule = load_schedule(season)
    start = pd.Timestamp.today().normalize()
    games = upcoming_games(
        schedule, start=start, end=start + pd.Timedelta(days=days - 1),
        only_scheduled=True, teams=teams or None,
    )
    if games.empty:
        st.info("No hay partidos programados con los filtros seleccionados.")
        return

    rows = []
    for game in games.itertuples(index=False):
        try:
            pred = predict_game(
                artifact, state, home=game.home, away=game.away,
                as_of=pd.Timestamp(game.game_date), line=225.5, warn_stale=False,
            )
        except (ValueError, KeyError):
            continue
        low, high = pred["interval_80"]
        rows.append({
            "FECHA": pd.Timestamp(game.game_date).strftime("%a, %d %b"),
            "HORA ET": "—" if pd.isna(game.game_time_et) else str(game.game_time_et),
            "PARTIDO": f"{game.away}  @  {game.home}",
            "TOTAL PROY.": round(float(pred["predicted_total"]), 1),
            "INTERVALO 80%": f"{low:.0f} – {high:.0f}",
            "PUNTOS LOCAL": round(float(pred["projected_home_score"]), 1)
                if pred.get("projected_home_score") is not None else "—",
            "PUNTOS VISITA": round(float(pred["projected_away_score"]), 1)
                if pred.get("projected_away_score") is not None else "—",
            "MARGEN PROY.": round(float(pred["predicted_margin"]), 1)
                if pred.get("predicted_margin") is not None else "—",
        })
    if rows:
        frame = pd.DataFrame(rows)
        st.dataframe(frame, hide_index=True, use_container_width=True, height=min(620, 42 + 42 * len(frame)))
        st.caption("Estimaciones del modelo Ridge e intervalo predictivo del 80%. No incluyen lesiones ni alineaciones confirmadas.")
    else:
        st.warning("No fue posible generar predicciones para los partidos filtrados.")


def main() -> None:
    st.set_page_config(page_title="NBA MODEL PRO", page_icon="🏀", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    try:
        artifact, state = _artifact(), _state()
    except FileNotFoundError as error:
        st.error(str(error))
        st.code("nba-ml all", language="bash")
        st.stop()

    teams = sorted(state["team_abbreviation"].dropna().unique())
    season = current_season()
    stale = pd.to_datetime(state["last_game_date"]).max().date()
    st.markdown(
        '<div class="pro-header"><div class="pro-logo">NBA</div><div>'
        '<div class="pro-title">NBA MODEL PRO <span class="pro-version">V3.4</span></div>'
        '<div class="pro-sub">Analítica predictiva · Totales · Rendimiento de equipos y jugadores</div>'
        '</div></div>', unsafe_allow_html=True,
    )

    tabs = st.tabs([
        "▣  Próximos Partidos", "▥  Análisis Táctico",
        "☑  Resultados y Validación", "♙  Líderes Jugadores",
        "✧  Rendimiento del Modelo", "▤  Datos",
    ])

    with tabs[0]:
        st.markdown("### Filtros")
        f1, f2, f3 = st.columns([1, 2, 1])
        days = f1.selectbox("Días", [3, 7, 14, 30], index=1)
        selected = f2.multiselect("Equipo", teams, default=[])
        if f3.button("↻  Actualizar", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        count = 0
        if schedule_path(season).exists():
            schedule = load_schedule(season)
            count = len(upcoming_games(
                schedule, start=pd.Timestamp.today().normalize(),
                end=pd.Timestamp.today().normalize() + pd.Timedelta(days=days - 1),
                only_scheduled=True, teams=selected or None,
            ))
        m1, m2, m3, m4 = st.columns(4)
        with m1: _metric("PARTIDOS", str(count), f"Programados ({days} días)")
        with m2: _metric("MODELO", str(artifact.get("model_name", "Ridge")), "Modelo activo")
        with m3: _metric("VARIABLES", str(len(artifact.get("features", []))), "Features del modelo")
        with m4: _metric("DATOS HASTA", stale.strftime("%d/%m/%Y"), "Último partido registrado")
        st.markdown("")
        _upcoming(artifact, state, days, selected)

    with tabs[1]:
        st.markdown("### Análisis de enfrentamiento")
        c1, c2, c3 = st.columns(3)
        home = c1.selectbox("Equipo local", teams, index=teams.index("BOS") if "BOS" in teams else 0)
        away_options = [t for t in teams if t != home]
        away = c2.selectbox("Equipo visitante", away_options, index=0)
        match_date = c3.date_input("Fecha", value=max(dt.date.today(), stale + dt.timedelta(days=1)))
        reference = st.number_input("Total de referencia para análisis", 150.0, 320.0, 225.5, 0.5)
        if home != away:
            try:
                result = predict_game(artifact, state, home=home, away=away, as_of=pd.Timestamp(match_date), line=float(reference))
                a, b, c, d = st.columns(4)
                with a: _metric("TOTAL PROYECTADO", f"{result['predicted_total']:.1f}", "Puntos combinados")
                with b: _metric("INTERVALO 80%", f"{result['interval_80'][0]:.0f}–{result['interval_80'][1]:.0f}", "Rango estimado")
                with c: _metric("MARGEN", f"{result.get('predicted_margin', 0):+.1f}", "Diferencia proyectada")
                with d: _metric("PROB. LOCAL", f"{result.get('home_win_probability', 0):.1%}", "Estimación del modelo")
                st.caption(f"Proyección de marcador: {result.get('projected_home_score', '—')} – {result.get('projected_away_score', '—')}")
                st.info("Estimaciones estadísticas experimentales; no incorporan lesiones, quintetos ni cambios de última hora.")
            except (ValueError, KeyError) as error:
                st.warning(str(error))

    with tabs[2]:
        _render_results(artifact, state, 225.5)
    with tabs[3]:
        _render_players()
    with tabs[4]:
        _render_performance()
    with tabs[5]:
        _render_data()


def run(port: int = 8502, open_browser: bool = True) -> None:
    import threading
    import webbrowser
    from pathlib import Path
    from streamlit.web import cli as streamlit_cli

    url = f"http://localhost:{port}"
    import sys
    sys.argv = ["streamlit", "run", str(Path(__file__).resolve()),
                f"--server.port={port}", "--server.headless=true"]
    if open_browser:
        threading.Timer(3.0, lambda: webbrowser.open(url)).start()
    raise SystemExit(streamlit_cli.main())


if __name__ == "__main__":
    main()

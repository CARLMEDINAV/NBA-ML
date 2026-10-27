# NBA Totals ML

**Demo pública:** https://nba-ml-flax.vercel.app

Predicción del **total de puntos** (over/under), hándicap y **props de jugadores**
(PTS, REB, AST y PRA) con datos NBA (`stats.nba.com` con respaldo de ESPN), un
pipeline reproducible y validación temporal estricta.

El proyecto descarga 11 temporadas regulares (2015-16 a 2025-26), construye
features pre-partido sin fuga de información, entrena varios modelos y deja un
artefacto listo para predecir enfrentamientos concretos.

---

## Resultado principal

Test **2025-26** (1.225 partidos, nunca usados en selección ni ajuste):

| Modelo | MAE | RMSE | R² | Acierto ±10 pts |
|---|---|---|---|---|
| Media de la liga | 18.09 | 22.98 | -0.334 | 34.5% |
| Forma reciente (baseline) | 15.35 | 19.30 | 0.059 | 40.7% |
| **Ridge (20 features)** | **15.39** | **19.28** | **0.061** | **39.3%** |

Validación walk-forward (entrenar con el pasado y predecir cada temporada):

| Temporada | MAE modelo | MAE forma reciente |
|---|---|---|
| 2021-22 | 14.86 | 14.82 |
| 2022-23 | 14.70 | 14.83 |
| 2023-24 | 14.63 | 14.71 |
| 2024-25 | 14.59 | 14.86 |
| 2025-26 | 15.39 | 15.35 |
| **Media** | **14.84** | **14.91** |

**Lectura honesta:** el modelo mejora un ~15% a predecir la media histórica de la
liga, pero queda **prácticamente empatado** con un baseline simple de forma
reciente. Eso es lo esperable: sin cuotas históricas de cierre, lesiones ni
alineaciones, el techo de información es bajo y el total de puntos tiene un
componente aleatorio enorme. El valor del proyecto está en el pipeline y en la
evaluación rigurosa, no en prometer una ventaja inexistente.

### Hándicap y ganador probable

El mismo set de 20 features alimenta un segundo Ridge sobre el **margen
local-visitante**. En test 2025-26:

| Métrica | Valor |
|---|---:|
| MAE del margen | 11.39 pts |
| RMSE | 14.62 pts |
| Acierto del ganador | 69.4% |
| Sigma walk-forward | 13.99 pts |

Con ese margen se estiman los puntos proyectados de cada equipo
(`total ± margen`), el ganador probable y un hándicap redondeado a 0.5. La
probabilidad de cubrir un hándicap de mercado usa la distribución empírica de
residuos del margen. **No es una línea oficial**: el hándicap sugerido es una
estimación del modelo. Las cuotas de over/under y la línea de mercado solo se
muestran cuando ESPN las devuelve para partidos próximos.

### Props de jugadores

Tres Ridge (`alpha=1`) predicen puntos, rebotes y asistencias por jugador; PRA es
la suma de las tres proyecciones. Validación temporal con el tramo final de
2025-26 (10.821 filas jugador-partido):

| Stat | MAE | RMSE | R² | Baseline MAE |
|---|---:|---:|---:|---:|
| PTS | 4.67 | 6.06 | 0.476 | 6.70 |
| REB | 1.92 | 2.53 | 0.413 | 2.55 |
| AST | 1.38 | 1.88 | 0.462 | 1.95 |
| PRA | 6.15 | 7.92 | 0.534 | 9.33 |

Sin lesiones, alineaciones ni cuotas de props. La pestaña **Props** muestra los
jugadores con minutos en 2026-27 ordenados por PRA proyectado. Al hacer clic en
un jugador se abre una ficha con sus últimos partidos, la línea ajustable, el
acierto histórico, el próximo partido y sus compañeros. Incluye minutos,
efectividad de tiro (FG%, 3P%, FT%, TS% y eFG%), uso estimado, H2H contra el
próximo rival, un posible defensor para ese partido y los defensores que más lo
marcaron según los datos de matchup de la NBA. Las barras comparan cada partido
con la línea; la fila **PRÓX.** es una proyección, no un resultado.

### Bet Builder

La pestaña **Bet Builder** permite construir una combinada con partidos y
mercados reales. No entrena un modelo nuevo: reutiliza el artefacto Ridge y sus
residuos walk-forward.

Mercados operativos:

| Mercado | Línea | Fuente de probabilidad |
|---|---|---|
| Total del partido (Over/Under) | Línea de ESPN/DraftKings si existe; si no, línea personalizada | Residuos del total |
| Ganador del partido | Sin línea | Residuos del margen |
| Hándicap | Solo si ESPN devuelve una línea; nunca se inventa | Residuos del margen |
| Total local / visitante | Línea personalizada | Residuos emparejados de total y margen |
| Jugadores (PTS, REB, AST, PRA, 3PM) | Deshabilitados | Faltan líneas de props y probabilidades calibradas por línea |

Reglas de cálculo:

- Selecciones de **partidos distintos**: probabilidad conjunta =
  `P1 × P2 × ... × Pn`, con un supuesto explícito de independencia.
- Selecciones del **mismo partido**: se muestran las probabilidades
  individuales, pero la conjunta queda como **no disponible** porque no hay un
  modelo de dependencia validado.
- La cuota justa teórica es `1 / P(conjunta)`. La cuota combinada de mercado
  solo aparece si todas las selecciones tienen cuota decimal verificada.
- El EV se calcula como `p × O − 1` solo con cuota decimal válida. Es una
  estimación bajo los supuestos del modelo, no una garantía de beneficio.
- Las líneas personalizadas se etiquetan como tales y no se presentan como
  líneas de mercado.
- El ticket se guarda en `localStorage` (`nbaTotalsBetBuilder.v1`) y se
  revalida contra el calendario, las líneas y las cuotas actuales al abrir la
  pestaña. Una selección antigua no se muestra como actual.

API:

```text
GET  /api/bet-builder/events?date=2026-10-27&days=1
POST /api/bet-builder/analyze
```

Ejemplo de análisis:

```json
{
  "selections": [
    {
      "event_id": "401810000",
      "event_date": "2026-10-27",
      "market": "total",
      "side": "OVER",
      "line": 222.5
    }
  ],
  "stake": 10,
  "decimal_odds": 1.91
}
```

Limitaciones: el modelo está entrenado hasta 2025-26 y no incorpora lesiones,
alineaciones, noticias ni cambios de cuota posteriores a la consulta. Las
probabilidades son frecuencias empíricas de residuos históricos, no
probabilidades calibradas como cuotas de una casa de apuestas. En líneas
enteras el empate (push) no se modela; se recomienda usar líneas terminadas
en .5.

---

## Estructura

```
nba-ml/
├── app.py                    # API FastAPI + frontend para Vercel
├── public/                   # frontend estático (HTML/CSS/JS)
├── artifacts/                # foto mínima de modelo/datos para Vercel
├── vercel.json
├── pyproject.toml            # paquete instalable + configuración de pytest
├── requirements.txt          # dependencias de la API (Vercel)
├── src/nba_ml/
│   ├── config.py             # rutas, temporadas, ventanas, splits
│   ├── cli.py                # nba-ml download|build|eda|train|predict|dashboard|all
│   ├── dashboard.py          # dashboard interactivo (Streamlit)
│   ├── eda.py                # análisis exploratorio -> reports/
│   ├── data/
│   │   ├── download.py       # nba_api con caché y reintentos
│   │   ├── build_dataset.py  # emparejado, features, Elo, estado por equipo
│   │   ├── schedule.py       # calendario y próximos partidos (ScheduleLeagueV2)
│   │   ├── players.py        # PTS/REB/AST/PRA (LeagueDashPlayerStats)
│   │   ├── player_logs.py    # game logs por jugador (PlayerGameLogs)
│   │   └── espn.py           # respaldo de actualización (stats, props y calendario)
│   ├── betting/
│   │   ├── probability.py    # validación, cuotas, EV y combinación independiente
│   │   └── markets.py        # probabilidades empíricas de total, ganador y hándicap
│   └── models/
│       ├── train.py          # tuning, validación, walk-forward, artefacto
│       ├── predict.py        # predicción y probabilidad over/under
│       ├── player_props.py   # PTS/REB/AST/PRA por jugador
│       └── results.py        # liquidación over/under contra resultados reales
├── public/bet-builder.js     # explorador de mercados, ticket y análisis
├── tests/test_pipeline.py    # 11 tests, incluidos los anti-leakage
├── tests/test_schedule.py    # normalización y filtros del calendario
├── tests/test_players.py     # cálculo de PRA y caché de jugadores
├── tests/test_results.py     # resultado real, acierto y métricas
├── tests/test_dashboard.py   # test de humo del dashboard
├── tests/test_api.py         # API FastAPI y frontend de Vercel
├── tests/test_betting_probability.py # matemática de Bet Builder
├── data/
│   ├── raw/                  # game logs, calendario y stats de jugadores (cache)
│   └── processed/            # games.csv, team_games.csv, team_state.csv
├── models/model.joblib       # modelo de totales + features + residuos
├── models/player_props.joblib # modelo de props + métricas
├── artifacts/player_state.csv # foto reciente de jugadores para props
├── artifacts/player_game_logs_recent.csv # hasta 40 partidos por jugador (ficha/H2H)
├── artifacts/player_defenders_recent.csv # defensores con más minutos de matchup
├── artifacts/player_bio.csv # altura y peso actual para estimar el rol defensivo
├── artifacts/team_games_recent.csv # historial compacto para el estado de equipos
└── reports/                  # metrics.md, eda.md, figuras, predicciones test
```

---

## Instalación

Requiere Python 3.11+ (probado con 3.12.7). El proyecto ya incluye un entorno
`.venv` creado con el Python de Anaconda.

Si necesitas recrearlo (en este equipo `python` no está en el PATH, usa la ruta
de Anaconda):

```powershell
cd $env:USERPROFILE\Desktop\nba-ml
& "$env:USERPROFILE\anaconda3\python.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,app,pipeline]"
```

`.[app]` instala Streamlit y `.[pipeline]` instala `nba_api` y matplotlib.
La API de Vercel solo necesita las dependencias base y FastAPI.

Si PowerShell bloquea la activación:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Versiones verificadas: `nba_api 1.11.4`, `pandas 2.3.3`, `numpy 2.4.6`,
`scikit-learn 1.7.2`, `matplotlib 3.11.2`.

---

## Uso

Pipeline completo (descarga cacheada, features, EDA, entrenamiento):

```powershell
.\.venv\Scripts\nba-ml.exe all
```

Comandos por separado:

```powershell
nba-ml download                 # 11 temporadas -> data/raw/
nba-ml build                    # -> data/processed/
nba-ml eda                      # -> reports/eda.md + figuras
nba-ml train                    # -> models/model.joblib + reports/metrics.md
nba-ml predict --home BOS --away LAL --date 2026-10-22 --line 225.5
nba-ml dashboard                # dashboard en http://localhost:8501
```

También puedes abrir el dashboard directamente:

```powershell
streamlit run src/nba_ml/dashboard.py --server.port 8501
```

### Dashboard

El dashboard de Streamlit tiene seis pestañas. La web pública añade **Análisis**
y **Props**; estas son las secciones principales:

- **Próximos partidos**: calendario de la temporada en curso (por defecto, los
  siguientes 7 días), filtro por equipo, total previsto, puntos proyectados de
  cada equipo, ganador probable, hándicap del modelo, intervalo 80% y
  probabilidad de over frente a la línea de referencia. El calendario se cachea
  en `data/raw/schedule_<temporada>.csv`; el botón **Actualizar** lo vuelve a
  descargar.
- **Resultados**: partidos ya jugados con total real, predicción, error, línea
  de referencia, resultado over/under, margen real, hándicap del modelo y
  acierto. Incluye MAE, sesgo, Brier y un gráfico de predicción vs total real.
  Por defecto muestra la temporada en curso; cambia a 2025-26 para el backtest
  completo (con margen y hándicap out-of-sample).
- **Jugadores**: ranking de puntos, rebotes, asistencias y **PRA** (PTS+REB+AST)
  por partido o en totales, con filtros por temporada, equipo, partidos jugados
  y búsqueda por nombre. Por defecto abre **2026-27** con los equipos actuales;
  la temporada **2025-26** es histórica y conserva el equipo que tenía cada
  jugador en esa temporada. Incluye foto oficial desde el CDN de la NBA (con
  iniciales como respaldo si la imagen no existe) y un gráfico de barras
  apiladas con el top 10. Los datos se cachean en
  `data/raw/player_stats_<temporada>_<modo>.csv`.
- **Props** (solo web): proyecciones de puntos, rebotes, asistencias y PRA por
  jugador para los próximos partidos, con filtros por días, equipo y número de
  jugadores por partido. Al abrir un jugador se muestra su ficha con los últimos
  partidos, línea ajustable, acierto histórico, próximo partido y compañeros.
  Añade minutos como estadística seleccionable, FG%, 3P%, FT%, TS%, eFG% y uso
  estimado, un resumen H2H contra el próximo rival y un posible defensor para
  ese partido. El posible defensor se elige por historial de matchup si el rival
  actual tiene un defensor con muestra; si no, por altura, rol y minutos. Es una
  estimación, no una asignación oficial. También se listan los defensores que
  más minutos de matchup acumularon. El uso se estima con los totales del equipo
  en cada box score; no incluye lesiones, alineaciones ni cuotas de props.
- **Predicción**: selectores de local/visitante, fecha y línea; total previsto,
  intervalo 80%, probabilidad de over, margen, puntos proyectados, ganador
  probable, hándicap del modelo, comparación de Elo/forma/descanso y gráfico de
  los últimos 10 totales de cada equipo.
- **Rendimiento**: informe `reports/metrics.md` y figuras de test, residuos,
  walk-forward e importancia de variables.
- **Datos**: resumen por temporada y figuras del EDA.

El modelo y los datos se cachean. Tras reentrenar, usa el botón
**Recargar datos y modelo** de la barra lateral.

En **Jugadores**, la temporada 2025-26 aparece como completa y la 2026-27 como
en curso. Al empezar la temporada solo hay uno o dos partidos por jugador, así
que las medias de la 2026-27 son todavía poco representativas.

En **Resultados**, la línea es la de referencia de la barra lateral, no la línea
de cierre real: `nba_api` no proporciona cuotas históricas. Sirve para comparar
el sesgo y el acierto direccional del modelo, no para afirmar que se habría
batido el cierre.

Si el calendario aún no se ha descargado, la pestaña de próximos partidos
muestra un botón para hacerlo. Ten en cuenta que las predicciones usan el último
estado disponible de los equipos; si la temporada acaba de empezar, conviene
actualizar los datos con `nba-ml download` y `nba-ml build`.

Ejemplo de salida:

```
BOS (local) vs LAL (visitante) - 2026-10-22
Total previsto: 225.8 puntos
Intervalo 80%: 202.5 - 250.3
Descanso: BOS 10 dias | LAL 10 dias
Linea 225.5: P(over) = 50.5% -> OVER
Modelo ridge | datos hasta 2025-26 | sigma residuos 18.7
```

`--date` y `--line` son opcionales. Sin fecha se asume el tercer día tras el
último partido de cada equipo. La probabilidad de over se estima **empíricamente**
con los residuos fuera de muestra del walk-forward (no se asume normalidad).

> Nota: los datos llegan hasta el final de la temporada 2025-26. Si predices un
> partido de la 2026-27, la herramienta avisa de que el estado de los equipos está
> desactualizado. Para actualizarlo, añade la temporada a `SEASONS` en
> `src/nba_ml/config.py` y ejecuta `nba-ml download --seasons 2026-27` (el CSV
> consolidado conserva las temporadas anteriores) y `nba-ml build`. Para refrescar
> una temporada ya cacheada usa `--force`.

---

## Demo pública en Vercel

La app pública está pensada para abrirla desde el móvil:

- **Frontend:** `public/` (HTML, CSS y JavaScript, sin build). Incluye una
  pestaña **Análisis** con la tabla partido a partido: puntos proyectados,
  total, posible ganador, hándicap sugerido y cuotas over/under; y una pestaña
  **Props** con PTS, REB, AST y PRA proyectados por jugador. Cada jugador tiene
  una ficha visual con sus últimos partidos, línea ajustable, acierto histórico,
  minutos, efectividad, uso, H2H y defensores.
- **API:** `app.py` con FastAPI (`/api/upcoming`, `/api/results`,
  `/api/players`, `/api/player-props`, `/api/player-card`, `/api/predict`).
  `/api/upcoming?odds=1&analysis=1` añade hándicap, proyecciones por equipo y
  cuotas de ESPN cuando están disponibles.
- **Datos:** `artifacts/` contiene una foto del modelo de totales, el modelo de
  props, el estado de los equipos y jugadores, el calendario, las estadísticas
  de jugadores y el backtest. Vercel no escribe en disco ni conserva la caché de
  `data/`.
- **Resultados en vivo:** al abrir la pestaña Resultados (temporada en curso),
  la API consulta el marcador de la NBA (`stats.nba.com`) con ESPN como
  respaldo y fusiona los partidos finalizados. La página se refresca sola cada
  60 segundos; si ambos fallan, usa el calendario cacheado.
- **Cuotas de mercado:** para partidos próximos, la API consulta el scoreboard
  de ESPN y extrae el hándicap, el total y las cuotas over/under de DraftKings
  cuando están publicadas. Se cachean 5 minutos y se marcan siempre como
  informativas; no hay histórico de líneas de cierre.
- **Persistencia:** un workflow de GitHub Actions
  (`.github/workflows/update-results.yml`) revisa los marcadores de ESPN cada
  15 minutos y fusiona los finales nuevos en
  `artifacts/schedule_2026_27.csv`. Después recalcula `artifacts/team_state.csv`
  con los boxscores de equipo de ESPN, para que el descanso, los promedios
  móviles y el Elo no se queden anclados en 2025-26. El calendario completo de la
  temporada se conserva; solo se actualizan las fechas cercanas a hoy.
- **Jugadores y props:** otro workflow
  (`.github/workflows/update-players.yml`) refresca cada 6 horas
  `player_stats_2026_27_pergame.csv`, `_totals.csv`, `player_state.csv`,
  `player_game_logs_recent.csv`, `player_defenders_recent.csv` y
  `player_bio.csv`. Las estadísticas, los boxscores recientes y el calendario
  salen de ESPN porque los runners de GitHub no alcanzan `stats.nba.com`. Así
  los equipos actuales (por ejemplo, Giannis en Miami), las medias móviles de
  los props, los últimos partidos y la efectividad/uso llegan a la web sin que
  Vercel tenga que consultar `stats.nba.com`. La biografía y los defensores
  conservan la foto anterior si la NBA no responde; el workflow lo avisa con
  una anotación de advertencia en lugar de dejar el artefacto obsoleto en
  silencio.

Para probarla en local:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --reload --port 8000
# abrir http://localhost:8000
```

Para actualizar la foto que ve Vercel después de descargar/entrenar:

```powershell
# Opcional: reentrenar props y refrescar su foto actual
.\.venv\Scripts\python.exe scripts\train_player_props.py
.\.venv\Scripts\python.exe scripts\update_web_player_props.py

.\.venv\Scripts\python.exe scripts\export_vercel_artifacts.py
git add artifacts
git commit -m "Actualiza artefactos de Vercel"
git push
```

Vercel detecta FastAPI desde `pyproject.toml`/`requirements.txt` y usa
`app.py` como entrypoint. La app de Streamlit no se puede ejecutar en Vercel
(necesita un servidor persistente con WebSockets); por eso la demo pública es
una versión FastAPI + frontend estático. El dashboard completo sigue disponible
en local con `nba-ml dashboard`.

> La demo usa una foto de los datos, no se actualiza sola. Las predicciones de
> la temporada en curso asumen el último estado disponible (2025-26) y avisan
> cuando los datos están desactualizados.

---

## Metodología

### Datos

- Fuente: `nba_api.stats.endpoints.LeagueGameLog` (temporada regular).
- 26.418 filas equipo-partido → **13.199 partidos**.
- Se excluyen **10 partidos de sede neutral** (NBA Mexico/Paris Games): la API
  marca a los dos equipos como visitantes y no hay forma fiable de asignar la
  ventaja de cancha.
- La 2019-20 (burbuja) y la 2020-21 (72 partidos) se incluyen tal cual.
- El target incluye prórrogas (es el total real anotado).

### Features (todas pre-partido)

- **Forma**: medias móviles de 5 y 10 partidos de puntos, puntos recibidos, pace
  (posesiones estimadas), rating ofensivo/defensivo, TS%, ritmo de triples,
  tiros libres, pérdidas y victorias.
- **Temporada**: medias expanding por temporada (mínimo 3 partidos).
- **Calendario**: días de descanso (recortados a 10), back-to-back, partidos en
  los últimos 7 y 14 días, número de partido de la temporada.
- **Elo**: rating pre-partido con ventaja de cancha (+100), ajuste por margen y
  regresión del 25% entre temporadas.
- **Contexto de liga**: media móvil del total anotado en la liga en ventanas de
  20, 50, 100 y 300 partidos. Es clave porque el anotado subió ~25 puntos desde
  2015-16.
- **Interacciones**: diferencia de Elo, descanso y ritmo, suma de ratings,
  total esperado (`pace × eficiencia`), forma conjunta, tasa de prórrogas y de
  partidos cerrados recientes.

**Prevención de fuga:** cada media móvil se calcula con `shift(1)`, Elo se guarda
antes de actualizarse y el contexto de liga usa solo partidos anteriores. Hay
tests que verifican que cambiar el resultado del partido actual o de partidos
futuros no altera sus features.

### Modelo y validación

- Split temporal: train ≤ 2023-24, validación 2024-25, test 2025-26.
- Búsqueda de hiperparámetros con `TimeSeriesSplit(3)` **solo dentro de train**.
- Comparación de Ridge, Random Forest y HistGradientBoosting en validación.
- Evaluación final reentrenando con train + validación.
- Walk-forward independiente (5 temporadas) para estimar el error fuera de
  muestra y calibrar los intervalos.
- Set parsimonioso de 20 features elegido por validación + walk-forward
  (14.84 MAE medio frente a 14.90 del set completo de 76).
- Segundo Ridge (`alpha=30`) sobre `home_margin` para el hándicap. Se evalúa
  con el mismo split temporal y walk-forward; sus residuos calibran
  `P(gana local)` y `P(cubre local)`.
- Artefacto final reentrenado con todas las temporadas disponibles. El script
  `scripts/update_margin_artifact.py` permite añadir el modelo de margen a un
  artefacto ya entrenado sin reentrenar el total.

### Props de jugadores

- **Datos:** game logs por jugador de 2023-24 a 2026-27
  (`PlayerGameLogs`), cacheados en `data/raw/player_game_logs_<temporada>.csv`.
- **Features:** medias móviles de 3, 5 y 10 partidos de minutos, PTS, REB, AST y
  PRA; tiros y pérdidas recientes; ritmo por minuto; partidos jugados en la
  temporada; descanso y back-to-back; localía; contexto ofensivo/defensivo y
  pace del equipo y del rival; y promedios de la temporada anterior.
- **Modelo:** tres Ridge (`alpha=1`) separados para PTS, REB y AST. **PRA =
  PTS + REB + AST** para que las cuatro columnas sean coherentes.
- **Validación temporal:** entrenamiento con partidos anteriores al
  `2026-02-01`; prueba con el resto de 2025-26 (10.821 filas de jugador-partido).
  MAE: **4.67 PTS**, **1.92 REB**, **1.38 AST**, **6.15 PRA**. La referencia
  ingenua (media del entrenamiento) da 6.70, 2.55, 1.95 y 9.33.
- **Producción:** se reentrena con todas las temporadas disponibles y se
  publica `artifacts/player_props.joblib`. `artifacts/player_state.csv` guarda
  la foto más reciente de cada jugador. El workflow de jugadores actualiza esa
  foto cada 6 horas; el modelo se reentrena manualmente con
  `python scripts/train_player_props.py`.
- **Límites:** no hay lesiones, alineaciones, límite de minutos ni cuotas de
  props. Se muestran los jugadores con minutos en la temporada actual, ordenados
  por PRA proyectado, no una rotación oficial. Si un equipo aún no ha jugado en
  2026-27, no aparece con proyecciones hasta que tenga minutos; la web lo avisa.

### Por qué Ridge

Con 13.000 partidos y mucho ruido, los modelos no lineales no aportan: Ridge
generaliza igual o mejor, es rápido, interpretable y sus coeficientes son
estables. La regularización fuerte (alpha≈30) actúa como shrinkage hacia la
media, que es exactamente lo que conviene en este problema.

---

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Cubren: emparejado local/visitante, exclusión de sedes neutrales, cálculo de
medias móviles, ausencia de fuga con resultados actuales/futuros, descanso y
back-to-back, consistencia del estado usado en predicción, contrato de features,
orden temporal de los splits, normalización y filtros del calendario, cálculo de
PRA y caché de jugadores, props por jugador, liquidación over/under y métricas
de acierto, margen y hándicap, la ficha visual de jugador, un test de humo del
dashboard y la API pública de Vercel, y la matemática del Bet Builder:
combinaciones independientes, rechazo de probabilidades/cuotas/líneas inválidas,
selecciones del mismo partido y cálculos de EV (79 en total).

---

## Limitaciones

- **Sin lesiones, sanciones ni alineaciones**: es la mayor fuente de información
  no capturada. Un modelo con disponibilidad de jugadores mejoraría el MAE de
  totales y, sobre todo, el de props. Los props tampoco modelan minutos ni
  rotaciones: usan el promedio reciente de minutos como contexto.
- **Sin histórico de cuotas**: solo se consultan cuotas actuales de ESPN para
  partidos próximos. No se puede evaluar ROI, CLV ni comparar contra el cierre
  histórico. `P(over)` y `P(cubre)` son probabilidades del modelo, no una
  ventaja de apuestas.
- **Solo temporada regular** y datos agregados por equipo; no se modela
  playoff, fatiga de viaje ni altitud.
- **Cambios de reglas y estilo de juego**: las medias de liga ayudan, pero un
  cambio brusco (p. ej. nuevo balón o reglas de faltas) degradaría el modelo.
- **Intervalos**: el 80% es empírico sobre residuos históricos; en temporadas
  atípicas la cobertura puede desviarse.

## Próximos pasos

1. Añadir disponibilidad de jugadores (lesiones/rotaciones) y un modelo de
   minutos para los props.
2. Incorporar líneas históricas de apertura/cierre y evaluar CLV.
3. Modelar prórrogas y ritmo por separado (pace × eficiencia).
4. Añadir viaje/distancia y altitud; 3 partidos en 4 días.
5. Calibrar intervalos por jugador para los props (hoy solo se publica el MAE).

## Fuente de datos

Datos obtenidos de la API pública de NBA (`stats.nba.com`) a través de
[`nba_api`](https://github.com/swar/nba_api). Uso educativo/analítico; respeta
los términos de uso de la NBA.

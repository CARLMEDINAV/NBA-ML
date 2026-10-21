# NBA Totals ML

**Demo pública:** https://nba-ml-flax.vercel.app

Predicción del **total de puntos** (over/under) de partidos NBA con datos oficiales
de `stats.nba.com`, un pipeline reproducible y validación temporal estricta.

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
reciente. Eso es lo esperable: sin cuotas de casas de apuestas, lesiones ni
alineaciones, el techo de información es bajo y el total de puntos tiene un
componente aleatorio enorme. El valor del proyecto está en el pipeline y en la
evaluación rigurosa, no en prometer una ventaja inexistente.

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
│   │   └── players.py        # PTS/REB/AST/PRA (LeagueDashPlayerStats)
│   └── models/
│       ├── train.py          # tuning, validación, walk-forward, artefacto
│       ├── predict.py        # predicción y probabilidad over/under
│       └── results.py        # liquidación over/under contra resultados reales
├── tests/test_pipeline.py    # 11 tests, incluidos los anti-leakage
├── tests/test_schedule.py    # normalización y filtros del calendario
├── tests/test_players.py     # cálculo de PRA y caché de jugadores
├── tests/test_results.py     # resultado real, acierto y métricas
├── tests/test_dashboard.py   # test de humo del dashboard
├── tests/test_api.py         # API FastAPI y frontend de Vercel
├── data/
│   ├── raw/                  # game logs, calendario y stats de jugadores (cache)
│   └── processed/            # games.csv, team_games.csv, team_state.csv
├── models/model.joblib       # modelo + features + residuos
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

Seis pestañas:

- **Próximos partidos**: calendario de la temporada en curso (por defecto, los
  siguientes 7 días), filtro por equipo, total previsto, intervalo 80% y
  probabilidad de over frente a la línea de referencia. El calendario se cachea
  en `data/raw/schedule_<temporada>.csv`; el botón **Actualizar** lo vuelve a
  descargar.
- **Resultados**: partidos ya jugados con total real, predicción, error, línea
  de referencia, resultado over/under y acierto del modelo. Incluye MAE, sesgo,
  Brier y un gráfico de predicción vs total real. Por defecto muestra la
  temporada en curso; cambia a 2025-26 para el backtest completo.
- **Jugadores**: ranking de puntos, rebotes, asistencias y **PRA** (PTS+REB+AST)
  por partido o en totales, con filtros por temporada, equipo, partidos jugados
  y búsqueda por nombre. Incluye un gráfico de barras apiladas con el top 10.
  Los datos se cachean en `data/raw/player_stats_<temporada>_<modo>.csv`.
- **Predicción**: selectores de local/visitante, fecha y línea; total previsto,
  intervalo 80%, probabilidad de over, comparación de Elo/forma/descanso y
  gráfico de los últimos 10 totales de cada equipo.
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

- **Frontend:** `public/` (HTML, CSS y JavaScript, sin build).
- **API:** `app.py` con FastAPI (`/api/upcoming`, `/api/results`,
  `/api/players`, `/api/predict`).
- **Datos:** `artifacts/` contiene una foto del modelo, el estado de los
  equipos, el calendario, las estadísticas de jugadores y el backtest. Vercel
  no escribe en disco ni conserva la caché de `data/`.

Para probarla en local:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --reload --port 8000
# abrir http://localhost:8000
```

Para actualizar la foto que ve Vercel después de descargar/entrenar:

```powershell
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
- Artefacto final reentrenado con todas las temporadas disponibles.

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
PRA y caché de jugadores, liquidación over/under y métricas de acierto, y un
test de humo del dashboard y la API pública de Vercel (33 en total).

---

## Limitaciones

- **Sin lesiones, sanciones ni alineaciones**: es la mayor fuente de información
  no capturada. Un modelo con disponibilidad de jugadores mejoraría el MAE.
- **Sin cuotas reales**: no se puede evaluar ROI ni comparar contra el mercado.
  `P(over)` es una probabilidad implícita del modelo, no una ventaja de apuestas.
- **Solo temporada regular** y datos agregados por equipo; no se modela
  playoff, fatiga de viaje ni altitud.
- **Cambios de reglas y estilo de juego**: las medias de liga ayudan, pero un
  cambio brusco (p. ej. nuevo balón o reglas de faltas) degradaría el modelo.
- **Intervalos**: el 80% es empírico sobre residuos históricos; en temporadas
  atípicas la cobertura puede desviarse.

## Próximos pasos

1. Añadir disponibilidad de jugadores (lesiones/rotaciones) vía player logs.
2. Incorporar líneas de apertura/cierre de casas de apuestas y evaluar CLV.
3. Modelar prórrogas y ritmo por separado (pace × eficiencia).
4. Añadir viaje/distancia y altitud; 3 partidos en 4 días.
5. Servir el modelo con una API pequeña (FastAPI) o un dashboard.

## Fuente de datos

Datos obtenidos de la API pública de NBA (`stats.nba.com`) a través de
[`nba_api`](https://github.com/swar/nba_api). Uso educativo/analítico; respeta
los términos de uso de la NBA.

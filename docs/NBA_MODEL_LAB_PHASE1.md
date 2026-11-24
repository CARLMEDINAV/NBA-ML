# NBA TOTALS 2.0 — Fase 1

## Alcance

Esta entrega añade un Prediction Tracker SQLite y un evaluador histórico puro,
sin modificar el entrenamiento, los artefactos ni las rutas actuales del modelo.

## Uso local

```python
from nba_ml.tracking import PredictionRecord, PredictionTracker
from nba_ml.evaluation import evaluate_predictions

tracker = PredictionTracker("data/nba_totals_tracker.sqlite3")
tracker.record_prediction(PredictionRecord(
    prediction_id="game-0022600001-ridge-totals-v1",
    game_id="0022600001",
    game_date="2026-11-24",
    home_team="BOS",
    away_team="LAL",
    predicted_at="2026-11-24T18:00:00-05:00",
    model_name="ridge",
    model_version="totals-v1",
    predicted_total=228.5,
    market_total_line=225.5,
    predicted_margin=4.5,
    home_handicap_line=-4.5,
    predicted_home_win_probability=0.63,
    features={"pace_sum": 198.2},
))
# Al recibir el marcador FINAL:
tracker.settle_game("0022600001", home_score=116, away_score=112)
metrics = evaluate_predictions(tracker.list_predictions(settled_only=True))
```

## Integridad

- `prediction_id` es clave primaria; reintentar el registro no duplica ni
  modifica la predicción original.
- Solo se admiten predicciones `pregame` en esta fase.
- El resultado se asigna una sola vez; no se sobrescribe una liquidación.
- El evaluador excluye partidos no finalizados y pushes del hit rate del mercado.
- Bias se define como `predicted_total - actual_total`: negativo significa
  subestimación.
- La línea de mercado es opcional y debe ser la línea real disponible al
  momento de la predicción. No se debe rellenar con una línea fija ficticia.

## Persistencia y despliegue

SQLite usa la biblioteca estándar y persiste en disco en entornos con disco
persistente. **El filesystem de Vercel Functions es efímero**: no apuntes la
base de datos a la carpeta del proyecto en producción. Antes de conectar esta
fase a la API pública, configura un volumen persistente o implementa el
adaptador PostgreSQL y sus credenciales como variables de entorno. No se
incluye una base efímera en Vercel ni se expone escritura pública.

## No incluido todavía

- Integración automática con `/api/predict` y `/api/results`.
- Sincronización automática con ESPN/NBA scoreboard.
- API de escritura autenticada y dashboard.
- Evaluación de predicciones live.
- Calibración y reentrenamiento automático.

Estas piezas quedan para la siguiente iteración, tras seleccionar y verificar
un backend persistente de producción.

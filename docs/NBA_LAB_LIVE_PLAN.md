# NBA TOTALS 2.0 — NBA Lab + Live Predictor

## Objetivo
Integrar dos herramientas dentro de una sola sección de la web, con pestañas internas:
- **NBA Lab:** simulación de escenarios prepartido usando el artefacto Ridge actual.
- **Live Predictor:** proyección intrapartido en modo automático (ESPN) y manual.

## Compatibilidad y no-regresión
- No se reemplaza ni reentrena `models/model.joblib`.
- Se reutilizan `load_artifact`, `load_team_state`, `predict_game` y `build_feature_row`.
- La nueva API vive en `src/nba_ml/lab_live_api.py` y se registra como router independiente desde `app.py`.
- Los endpoints existentes de calendario, predicción, props, lesiones, notificaciones y Bet Builder permanecen sin cambios.

## Endpoints añadidos
| Método | Endpoint | Uso |
|---|---|---|
| POST | `/api/lab/simulate` | Baseline Ridge y escenario What-if |
| GET | `/api/live/predict?event_id=...` | Live automático consultando el resumen ESPN |
| POST | `/api/live/predict/manual` | Proyección desde marcador/reloj ingresados manualmente |

## NBA Lab
- Ritmo: cambio porcentual aplicado a las variables de pace de ambos equipos.
- Eficiencia: ajustes separados al rating ofensivo local y visitante.
- Minutos: variación de minutos de un jugador clave por equipo, convertida a puntos con PTS/minuto ingresados por el usuario.
- Comparación: baseline vs escenario, total, marcador por equipo, margen y probabilidad empírica Over cuando se proporciona línea.
- La salida explica que el ajuste de minutos es un overlay explícito, no un modelo de rotaciones.

## Live Predictor
- Modo automático: lee eventos activos con el endpoint ya existente `/api/live/games`, y solicita el resumen del evento a ESPN.
- Modo manual: acepta equipos, puntos, período, reloj y total prepartido.
- Refresco automático cada 15 segundos mientras la sección está visible.
- Proyección híbrida: ritmo observado extrapolado al tiempo reglamentario y mezclado con el total prepartido mediante shrinkage. El peso del ritmo observado aumenta con el tiempo transcurrido.
- La estimación no se presenta como probabilidad calibrada ni como línea de mercado.

## Archivos
- `src/nba_ml/lab_live_api.py`: API, validación, escenario ML y estimador live.
- `app.py`: registro del router.
- `public/index.html`: pestaña integrada y controles.
- `public/lab-live.js`: interacción y polling.
- `public/lab-live.css`: estilos responsive.
- `docs/NBA_LAB_LIVE_PLAN.md`: arquitectura y límites.

## Próxima iteración recomendada
1. Agregar tests unitarios de simulación y reloj (incluido overtime).
2. Añadir fallback de ESPN para resumen live cuando el proveedor no entrega período/reloj.
3. Incorporar selector de jugadores con PTS/minuto derivado de los game logs recientes, conservando la opción de ajuste manual.
4. Entrenar y validar un modelo live con snapshots históricos de play-by-play; no reutilizar Ridge prepartido como si fuera un modelo in-game.
5. Medir MAE por período y calibración de intervalos antes de publicar probabilidades de Over/Under en vivo.

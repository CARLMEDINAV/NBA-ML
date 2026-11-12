# Artefactos de despliegue

Esta carpeta contiene la copia inicial de modelos y datos que se utiliza al
crear el servicio. En Render, `scripts/start_render.py` la copia al disco
persistente montado en `/var/data/artifacts` cuando los archivos todavía no
existen y enlaza esta ruta con el directorio persistente.

- `model.joblib`: modelo de totales, features y residuos.
- `player_props.joblib`: modelo de props.
- `team_state.csv` y `team_games_recent.csv`: estado e historial reciente de equipos.
- `schedule_2026_27.csv` y `pregame_predictions_2026_27.csv`: calendario, resultados y predicciones prepartido.
- `player_stats_*.csv`, `player_state.csv`, `player_game_logs_recent.csv`, `player_defenders_recent.csv` y `player_bio.csv`: estadísticas y contexto de jugadores.

Los procesos programados viven dentro del servicio FastAPI de Render y escriben
en el mismo disco persistente. Los archivos existentes en producción no se
sobrescriben al desplegar una nueva versión; solo se copian archivos iniciales
que falten.

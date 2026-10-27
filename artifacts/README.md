# Artefactos de despliegue

Esta carpeta contiene la foto minima que necesita la app publica de Vercel:

- `model.joblib`: modelo Ridge entrenado, lista de features y residuos.
- `team_state.csv`: ultimo estado conocido de cada equipo (incluye descanso y Elo actualizados).
- `team_games_recent.csv`: historial compacto usado para recalcular `team_state.csv`.
- `schedule_2026_27.csv`: calendario regular y resultados finalizados.
- `player_stats_<temporada>_<modo>.csv`: PTS/REB/AST/PRA por jugador.
- `test_predictions.csv`: predicciones fuera de muestra de 2025-26.

No se editan a mano. Se regeneran con:

```powershell
.\.venv\Scripts\python.exe scripts\export_vercel_artifacts.py
```

Después hay que hacer commit y push para que Vercel vuelva a desplegar. Los
archivos completos de `data/` y `models/` siguen fuera de Git.

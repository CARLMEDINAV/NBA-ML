# Despliegue de NBA Totals: Vercel + Render

## Arquitectura

- **Vercel** sirve únicamente los archivos estáticos de `public/`.
- **Render Web Service** ejecuta FastAPI, el modelo de predicción, lesiones, jugadores y Props.
- **Disco persistente de Render** conserva `artifacts/` entre reinicios y despliegues.
- **Planificador interno de FastAPI** ejecuta los procesos de actualización en el mismo servicio que tiene montado el disco:
  - Resultados, calendario, estado de equipos y snapshots prepartido: cada 15 minutos.
  - Estadísticas de jugadores, estado de Props y defensores: a las 00:00, 06:00, 12:00 y 18:00 UTC.

Los workflows de actualización de GitHub Actions quedan disponibles solo para ejecución manual. CI sigue ejecutando las pruebas automáticamente.

## Primera instalación

1. Fusionar el PR de migración a `main`.
2. En Render, seleccionar **New → Blueprint** y conectar `CARLMEDINAV/NBA-ML`, rama `main`.
3. Revisar el Blueprint `render.yaml`. Crea el servicio `nba-totals-api`, plan de 1 CPU / 2 GB de RAM y disco persistente de 1 GB montado en `/var/data`. Este servicio con disco es de pago; confirmar el coste mostrado por Render antes de crear.
4. Esperar a que termine el primer deploy.
5. Comprobar `https://nba-totals-api.onrender.com/api/health`. Debe devolver `{"status":"ok", ...}`.
6. En Vercel, conservar el proyecto actual y desplegar el mismo repositorio. `vercel.json` ya indica `public` como directorio de salida y envía `/api/*` a Render.
7. Verificar la página principal y las secciones Próximos, Análisis, Resultados, Jugadores, Lesiones y Props.

## Almacenamiento persistente

`scripts/start_render.py` copia al disco los artefactos versionados que todavía no existan y convierte `artifacts/` en un enlace al disco persistente. Los modelos versionados (`model.joblib`, `player_props.joblib`), el backtest de entrenamiento y las estadísticas históricas 2025-26 se sincronizan en cada deploy. Los CSV de temporada en curso, estados e historiales actualizados por el scheduler se preservan; solo se copian si todavía faltan.

El servicio usa una sola instancia porque el disco persistente está adjunto a esa instancia. No escalar horizontalmente ni cambiar el punto de montaje sin diseñar antes una estrategia de almacenamiento compartido.

## Variables configuradas en el Blueprint

- `NBA_ENABLE_SCHEDULER=1`: habilita las actualizaciones programadas.
- `NBA_ARTIFACTS_DIR=/var/data/artifacts`: ubicación persistente de modelos y CSV.
- `NBA_BIO_UPDATE=0`: evita una consulta de biografía que no es necesaria para cada actualización.
- `CORS_ORIGINS`: dominio de Vercel permitido.
- `PYTHON_VERSION=3.12.10`: runtime Python.

No se necesitan claves secretas para el despliegue base.

## Comprobaciones y operación

- Health check: `GET /api/health`.
- Documentación interactiva: `/docs`.
- Los logs de Render muestran el inicio y resultado de cada script de actualización.
- Si un proceso de actualización falla, APScheduler registra el error; el servicio API permanece activo y conserva los artefactos anteriores.
- Las actualizaciones se ejecutan secuencialmente y no se solapan dentro de cada tarea.
- El disco persistente impide los despliegues zero-downtime y limita el servicio a una instancia; Render detiene la instancia anterior durante el cambio de versión.

## Rollback

Si el nuevo servicio no supera el health check, no cambiar el dominio del frontend ni borrar el despliegue actual de Vercel. Corregir o revertir el deploy de Render desde su historial. La interfaz estática y el backend quedan desacoplados.

# PROP LAB — Especificación técnica MVP

## Decisiones de alcance

- Repositorio: `CARLMEDINAV/NBA-ML`.
- Rama de trabajo: `feature/nba-model-lab-2.0`.
- Frontend: HTML/CSS/JavaScript estático en `public/`, servido por FastAPI/Vercel.
- Backend: FastAPI en `app.py`, lógica de dominio en `src/nba_ml/`.
- Datos: artefactos CSV/JOBLIB existentes en `artifacts/`.
- Mercados MVP: PTS, REB, AST, 3PM y PRA.
- Cuotas: no integrar proveedor externo. Reutilizar líneas introducidas en el analizador actual y las cuotas que ya existan en la app; no simular cuotas de jugador ni atribuir cuotas de partido a props.
- Persistencia MVP: artefactos actuales y seguimiento local/servidor según el mecanismo ya disponible. No introducir Supabase como requisito de esta fase.

## Estado real del repositorio (base para el diseño)

El proyecto ya contiene:

- `src/nba_ml/models/player_props.py`: modelos Ridge para PTS, REB y AST; PRA se calcula como suma de las tres proyecciones. Las features usan ventanas móviles 3/5/10, descanso, localía y contexto reciente del equipo/rival.
- `scripts/train_player_props.py`: entrenamiento temporal y publicación de `artifacts/player_props.joblib` y `artifacts/player_state.csv`.
- `scripts/update_web_player_props.py`: actualización del histórico reciente desde ESPN y reconstrucción del estado de jugadores.
- `artifacts/player_game_logs_recent.csv`: historial reciente con `fg3m` y `fg3a`, entre otras estadísticas.
- `app.py`: FastAPI, carga cacheada de artefactos, calendario, lesiones, recomendaciones de props y otros módulos.
- `public/index.html` y `public/app.js`: navegación y analizador Props ya existentes. La interfaz existente contempla PTS, REB, AST, PRA y 3PM, además de otros mercados.
- El modelo actual declara `PROP_TARGETS = ("pts", "reb", "ast")`; 3PM no tiene todavía un modelo dedicado.
- La documentación del modelo declara que no incorpora lesiones, alineaciones, minutos proyectados propios ni cuotas de props. No presentar esos factores como modelados.

**Consecuencia:** PROP LAB será una capa de descubrimiento y presentación sobre el motor actual, no una segunda aplicación ni una sustitución del analizador Props.

## Objetivo funcional

Mostrar un tablero diario de oportunidades estadísticas de jugadores. Cada tarjeta debe explicar la tendencia observada, la proyección del modelo, la línea comparada y los factores que realmente se pueden respaldar con datos.

### Pestañas

1. **Props:** listado de selecciones filtrables por partido, jugador y mercado.
2. **Tendencias:** rachas y frecuencia de cumplimiento en L5, L10 y L15.
3. **Valor:** comparación de la proyección con una línea disponible y, solo si existe cuota real, cálculo de valor esperado. Si no hay cuota de jugador, mostrar “Cuota no disponible”; no inventarla.

### Filtros

- Mercado: PTS, REB, AST, 3PM, PRA.
- Calidad de muestra: L5/L10/L15 y mínimo de partidos.
- Tendencia: Over / Under / todas.
- Umbral de frecuencia: 7/10, 8/10, 9/10 y 10/10.
- Partido, equipo y búsqueda por jugador.
- Línea: valor introducido por el usuario o línea realmente obtenida de una fuente ya integrada.

## Diseño de tarjeta

Cada tarjeta incluye:

- Foto/cabecera del jugador, equipo, rival y hora del partido.
- Mercado y línea (si existe).
- Proyección del modelo (si existe para ese mercado).
- Diferencia proyección − línea.
- Aciertos Over/Under L5, L10 y L15, con denominador visible.
- Promedio observado L5/L10/L15.
- Historial visual de resultados recientes.
- Estado de disponibilidad si existe un reporte ESPN verificable, con hora de actualización.
- Etiquetas de contexto basadas solo en datos presentes: minutos recientes, descanso, localía y ritmo/contexto de equipo cuando esté disponible.
- Fuente y fecha de actualización de los datos.

No usar “Muy buena”, “9/10” o colores verdes como probabilidad de ganar. Un porcentaje histórico es frecuencia pasada, no probabilidad futura. El MVP puede mostrar frecuencia descriptiva y una etiqueta de señal separada, documentada y no probabilística.

## Modelado

### PTS / REB / AST

Reutilizar los modelos Ridge y sus artefactos actuales, sin cambiar el modelo de totales de partido. Exponer MAE/RMSE y fecha de entrenamiento cuando estén disponibles.

### PRA

Mantener la definición actual: PTS proyectados + REB proyectados + AST proyectados. No entrenar un modelo PRA independiente en el MVP.

### 3PM

Añadir un cuarto objetivo Ridge específico usando `fg3m` como target y features disponibles antes del partido. Añadir features rolling de `fg3m` y `fg3a` (ventanas 3/5/10) y mantener el corte temporal de validación. No entrenar con estadísticas del propio partido que se está prediciendo. Comparar contra un baseline simple y publicar MAE/RMSE/R² y n de validación. Si el artefacto nuevo no existe o la métrica no supera el baseline, mostrar tendencia histórica pero ocultar proyección 3PM hasta resolverlo.

### Tendencias

Calcular sobre game logs ordenados cronológicamente, por jugador y temporada. Para cada mercado y línea:

- Over: valor real > línea.
- Under: valor real < línea.
- Push: valor real = línea; se informa aparte y no cuenta como acierto ni fallo.
- Frecuencia = aciertos / (partidos válidos sin push).
- Mostrar siempre aciertos, partidos válidos y pushes.
- No mezclar temporadas por defecto; ofrecer temporada actual y, opcionalmente, historial extendido como filtro separado.
- No contar partidos con minutos nulos o cero como observaciones válidas.

## API propuesta

Crear router independiente `src/nba_ml/prop_lab_api.py` y registrarlo en `app.py`, manteniendo intactas las rutas existentes.

| Método | Ruta | Función |
|---|---|---|
| GET | `/api/prop-lab/today` | Tarjetas de los partidos próximos del día |
| GET | `/api/prop-lab/trends` | Tendencias por jugador/mercado/línea |
| GET | `/api/prop-lab/player/{player_id}` | Detalle de historial y proyecciones |
| GET | `/api/prop-lab/health` | Disponibilidad/fecha de artefactos y fuentes |

Parámetros sugeridos para `today`: `date`, `market`, `team`, `game_id`, `min_sample`, `limit`.

Parámetros sugeridos para `trends`: `player_id`, `market`, `line`, `side`, `window` (5/10/15).

Respuesta de tarjeta (contrato orientativo):

```json
{
  "player_id": 123,
  "player_name": "Nombre",
  "team": "BOS",
  "opponent": "NYK",
  "market": "PTS",
  "line": null,
  "projection": 21.4,
  "projection_source": "ridge_player_props",
  "line_source": null,
  "windows": {
    "5": {"over": 3, "under": 2, "push": 0, "valid": 5, "average": 22.0},
    "10": {"over": 6, "under": 4, "push": 0, "valid": 10, "average": 21.7},
    "15": {"over": 9, "under": 6, "push": 0, "valid": 15, "average": 21.2}
  },
  "availability": {"status": "unknown", "updated_at": null},
  "data_as_of": "YYYY-MM-DD"
}
```

El ejemplo es un esquema, no datos reales. Omitir o devolver null cuando un campo no tenga fuente.

## Frontend

Añadir pestaña superior **PROP LAB** sin eliminar ni renombrar la pestaña **Props** existente hasta verificar equivalencia funcional. El módulo nuevo puede integrar el analizador actual como vista de detalle.

Archivos nuevos propuestos:

- `public/prop-lab.js`: fetch, filtros, renderizado de tarjetas, estados loading/error/empty y actualización manual.
- `public/prop-lab.css`: tarjetas responsive con estética oscura verde/turquesa.
- `src/nba_ml/prop_lab_api.py`: endpoints y cálculo descriptivo de tendencias.
- `tests/test_prop_lab.py`: pruebas de ventanas, pushes, líneas, orden cronológico y mercados.

Archivos a modificar:

- `app.py`: incluir router y dependencias mínimas.
- `public/index.html`: nueva pestaña y contenedor, referencias versionadas a JS/CSS.
- `scripts/train_player_props.py` y `src/nba_ml/models/player_props.py`: añadir objetivo 3PM y features temporales.
- `scripts/update_web_player_props.py`: asegurar que `fg3m` y `fg3a` se mantengan en el histórico y estado requerido.

## No-regresión

- No tocar el modelo Ridge de totales, sus features ni sus artefactos.
- No reemplazar NBA Lab + Live, Bet Builder, lesiones, Resultados ni el analizador Props.
- No cambiar la navegación existente salvo añadir la nueva pestaña.
- No añadir dependencias externas en el MVP.
- No agregar cuotas de props externas ni Supabase en esta fase.
- Mantener el funcionamiento si faltan artefactos: devolver estado parcial explícito y no bloquear el resto de NBA TOTALS.

## Plan de implementación

1. **Auditoría y tests:** localizar los endpoints/JS actuales de Props; crear tests unitarios de tendencias y contrato.
2. **Backend tendencias:** router independiente que consume el histórico ya publicado.
3. **Modelo 3PM:** features rolling sin leakage, validación temporal, baseline y artefacto versionado.
4. **Proyecciones + tarjetas:** conectar PTS/REB/AST/PRA existentes y 3PM cuando supere validación.
5. **Frontend:** pestañas Props/Tendencias/Valor, tarjetas responsive y filtros.
6. **Verificación:** pytest, pruebas de API, smoke test de frontend y revisión de no-regresión.

## Criterios de aceptación del MVP

- Los cinco mercados aparecen como filtros y pueden consultarse.
- PTS/REB/AST usan el modelo ya existente; PRA se calcula como suma.
- 3PM muestra tendencia histórica desde `fg3m`; solo muestra proyección si el nuevo modelo pasa la validación temporal.
- Las ventanas L5/L10/L15 coinciden con los game logs y tratan pushes correctamente.
- No se presenta frecuencia histórica como probabilidad calibrada.
- No se muestran cuotas inexistentes.
- La interfaz funciona en móvil y escritorio.
- Todas las rutas y módulos actuales siguen funcionando.

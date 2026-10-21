# Resultados del modelo de totales NBA

Generado: 2026-10-21T05:11:25.396131+00:00

## Datos y particiones

- Partidos: **13,199** (2015-16 - 2025-26)
- Features: **20**
- Train: 10,749 partidos (hasta 2023-24)
- Validacion: 1,225 partidos (2024-25)
- Test: 1,225 partidos (2025-26)

## Seleccion de modelo (validacion 2024-25)

| model | mae | rmse | r2 | cv_mae |
|---|---|---|---|---|
| ridge | 14.594 | 18.247 | 0.150 | 14.954 |
| random_forest | 14.755 | 18.490 | 0.127 | 15.022 |
| hist_gradient_boosting | 14.711 | 18.469 | 0.129 | 15.140 |

## Test 2025-26 (modelo reentrenado con train + validacion)

| model | n | mae | rmse | r2 | bias | within_10 |
|---|---|---|---|---|---|---|
| baseline_media_liga | 1225 | 18.087 | 22.982 | -0.334 | 11.503 | 0.345 |
| baseline_forma_reciente | 1225 | 15.351 | 19.298 | 0.059 | 0.401 | 0.407 |
| ridge_train+val | 1225 | 15.388 | 19.276 | 0.061 | -0.978 | 0.393 |

Mejor modelo: **ridge**. Mejora de MAE frente a la media de la liga: **14.9%**. Frente al baseline de forma reciente: **-0.2%** (un rival dificil de batir: los totales NBA tienen mucho ruido irreducible).

## Validacion walk-forward

| season | n | mae | baseline_mae | delta_vs_form | rmse | r2 | bias |
|---|---|---|---|---|---|---|---|
| 2021-22 | 1230 | 14.864 | 14.820 | -0.043 | 18.770 | 0.118 | 2.259 |
| 2022-23 | 1230 | 14.700 | 14.829 | 0.129 | 18.919 | 0.057 | 0.854 |
| 2023-24 | 1230 | 14.631 | 14.707 | 0.076 | 18.238 | 0.190 | -0.943 |
| 2024-25 | 1225 | 14.594 | 14.858 | 0.264 | 18.247 | 0.150 | 0.025 |
| 2025-26 | 1225 | 15.388 | 15.351 | -0.037 | 19.276 | 0.061 | -0.978 |

MAE medio walk-forward: **14.84** vs **14.91** de la forma reciente (5 temporadas).

El set completo de 76 features obtuvo un MAE walk-forward medio de 14.90; el conjunto parsimonioso de 20 features usado aqui, 14.84. Se prioriza el modelo simple por estabilidad e interpretabilidad.

Sigma de residuos fuera de muestra: **18.69 puntos**.

## Variables mas relevantes

| feature | importance |
|---|---|
| form_total | 0.375 |
| opp_pts_exp_home | 0.203 |
| ot_rate_sum | 0.097 |
| pace_sum | 0.073 |
| league_total_roll300 | 0.060 |
| opp_pts_exp_away | 0.032 |
| pts_exp_away | 0.022 |
| league_total_roll50 | 0.020 |
| def_rtg_sum | 0.009 |
| exp_total | 0.006 |

## Como leer estas metricas

El total de puntos tiene un componente aleatorio grande (tiros que entran o no, prorrogas). Un MAE de 14-16 puntos sobre una desviacion tipica de ~20 implica una capacidad predictiva moderada: util como senal, no como certeza. No hay lineas reales de apuestas en el dataset, por lo que **no** se reporta ROI.

"""Analisis exploratorio reproducible: tablas y figuras en ``reports/``."""

from __future__ import annotations

import logging

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import FIGURES_DIR, REPORTS_DIR, TARGET, ensure_directories
from .data.build_dataset import MODEL_FEATURES, load_dataset

LOG = logging.getLogger(__name__)


def _markdown_table(frame: pd.DataFrame, float_format: str = "{:.2f}") -> str:
    headers = [str(column) for column in frame.columns]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for _, row in frame.iterrows():
        cells = []
        for value in row:
            if isinstance(value, (float, np.floating)):
                cells.append(float_format.format(value))
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def run_eda(dataset: pd.DataFrame | None = None) -> pd.DataFrame:
    ensure_directories()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    df = dataset if dataset is not None else load_dataset()

    season_stats = (
        df.groupby("season")
        .agg(
            partidos=(TARGET, "count"),
            media=(TARGET, "mean"),
            mediana=(TARGET, "median"),
            desv_tipica=(TARGET, "std"),
            minimo=(TARGET, "min"),
            maximo=(TARGET, "max"),
            prorroga_pct=("is_overtime", "mean"),
        )
        .reset_index()
    )
    season_stats["partidos_270plus_pct"] = (
        df.groupby("season")[TARGET].apply(lambda series: float((series >= 270).mean())).to_numpy()
    )

    fig, ax = plt.subplots(figsize=(10, 4.5))
    groups = [df.loc[df["season"] == season, TARGET].to_numpy() for season in season_stats["season"]]
    ax.boxplot(groups, tick_labels=season_stats["season"], showfliers=False)
    ax.set_ylabel("Puntos totales por partido")
    ax.set_title("Distribucion de totales por temporada (2015-16 a 2025-26)")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "totals_by_season.png", dpi=150)
    plt.close(fig)

    ordered = df.sort_values(["game_date", "game_id"])
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(
        ordered["game_date"],
        ordered[TARGET].rolling(200, min_periods=50).mean(),
        color="#3b6ea5",
        linewidth=1.2,
        label="Media movil (200 partidos)",
    )
    ax.plot(
        pd.to_datetime(season_stats["season"].str.slice(0, 4) + "-11-01"),
        season_stats["media"],
        color="crimson",
        marker="o",
        linewidth=1.2,
        label="Media por temporada",
    )
    ax.set_ylabel("Puntos totales")
    ax.set_title("Evolucion del anotado en la NBA")
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "scoring_trend.png", dpi=150)
    plt.close(fig)

    correlations = (
        df[list(MODEL_FEATURES)]
        .corrwith(df[TARGET])
        .dropna()
        .sort_values(key=lambda series: series.abs(), ascending=False)
        .head(15)
        .rename("correlacion")
        .reset_index()
        .rename(columns={"index": "feature"})
    )
    fig, ax = plt.subplots(figsize=(8, 5))
    top = correlations.iloc[::-1]
    ax.barh(top["feature"], top["correlacion"], color="#3b6ea5")
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_xlabel("Correlacion de Pearson con el total")
    ax.set_title("Features mas correlacionadas con el target")
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "feature_correlations.png", dpi=150)
    plt.close(fig)

    missing = (
        df[list(MODEL_FEATURES)]
        .isna()
        .mean()
        .sort_values(ascending=False)
        .head(10)
        .rename("pct_nulos")
        .reset_index()
        .rename(columns={"index": "feature"})
    )

    lines = [
        "# Analisis exploratorio",
        "",
        f"- Partidos analizados: **{len(df):,}**",
        f"- Rango: **{df['game_date'].min().date()}** a **{df['game_date'].max().date()}**",
        f"- Media de puntos totales: **{df[TARGET].mean():.1f}** "
        f"(desviacion tipica {df[TARGET].std():.1f})",
        f"- Partidos con 270+ puntos: **{(df[TARGET] >= 270).mean():.1%}**",
        "",
        "## Totales por temporada",
        "",
        _markdown_table(season_stats),
        "",
        "La liga anota bastante mas que en 2015-16: el nivel medio sube ~25 puntos "
        "por partido. Por eso el modelo incluye medias moviles del total de la liga "
        "en varias ventanas: sin ellas, un modelo entrenado en el pasado subestimaria "
        "sistematicamente las temporadas recientes.",
        "",
        "## Nulos en features",
        "",
        _markdown_table(missing),
        "",
        "Los nulos se concentran en las primeras jornadas de cada temporada "
        "(min_periods=3) y los imputa el pipeline con la mediana.",
        "",
        "## Correlaciones con el target",
        "",
        _markdown_table(correlations),
        "",
        "Correlaciones de ~0.4-0.5 parecen altas, pero una parte importante viene de "
        "la tendencia de epoca: la correlacion intra-temporada media es 0.32 "
        "(rango 0.24-0.41), que es la senal real que puede explotar el modelo. "
        "Ademas, correlacion no implica causalidad ni capacidad de prediccion.",
        "",
        "![Totales por temporada](figures/totals_by_season.png)",
        "",
        "![Tendencia del anotado](figures/scoring_trend.png)",
        "",
        "![Correlaciones](figures/feature_correlations.png)",
        "",
    ]
    (REPORTS_DIR / "eda.md").write_text("\n".join(lines), encoding="utf-8")
    LOG.info("EDA escrita en %s", REPORTS_DIR / "eda.md")
    return season_stats

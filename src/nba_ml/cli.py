"""Interfaz de linea de comandos: ``python -m nba_ml <comando>``."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import SEASONS
from .data.build_dataset import build_dataset
from .data.download import download
from .eda import run_eda
from .models.predict import format_prediction, load_artifact, load_team_state, predict_game
from .models.train import train

LOG = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nba-ml",
        description="Prediccion de totales de puntos (over/under) en la NBA.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Log detallado")
    subparsers = parser.add_subparsers(dest="command", required=True)

    download_parser = subparsers.add_parser(
        "download", help="Descarga los game logs desde stats.nba.com"
    )
    download_parser.add_argument(
        "--seasons",
        nargs="+",
        default=list(SEASONS),
        help="Temporadas a descargar/actualizar, p. ej. 2024-25 2025-26",
    )
    download_parser.add_argument(
        "--force", action="store_true", help="Ignora la cache local y vuelve a descargar"
    )

    subparsers.add_parser("build", help="Empareja partidos y construye features")
    subparsers.add_parser("eda", help="Genera tablas y figuras de analisis exploratorio")
    subparsers.add_parser("train", help="Entrena, evalua y guarda el modelo")

    predict_parser = subparsers.add_parser(
        "predict", help="Predice el total de un enfrentamiento"
    )
    predict_parser.add_argument("--home", required=True, help="Equipo local (BOS o nombre)")
    predict_parser.add_argument("--away", required=True, help="Equipo visitante")
    predict_parser.add_argument("--date", help="Fecha del partido (YYYY-MM-DD)")
    predict_parser.add_argument("--line", type=float, help="Linea de over/under")

    dashboard_parser = subparsers.add_parser(
        "dashboard", help="Abre el dashboard interactivo (Streamlit)"
    )
    dashboard_parser.add_argument("--port", type=int, default=8501, help="Puerto local")

    all_parser = subparsers.add_parser("all", help="Pipeline completo")
    all_parser.add_argument("--force", action="store_true", help="Fuerza la descarga")

    return parser


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)

    try:
        if args.command == "dashboard":
            from .dashboard import run

            run(port=args.port)
            return 0

        if args.command in ("download", "all"):
            download(
                seasons=args.seasons if args.command == "download" else SEASONS,
                force=args.force,
            )

        if args.command in ("build", "all"):
            build_dataset()

        if args.command in ("eda", "all"):
            run_eda()

        if args.command in ("train", "all"):
            results = train()
            metrics = results["test"]["metrics"]
            print(
                f"\nTest {results['dataset']['last_season']}: "
                f"MAE={metrics['mae']:.2f} | RMSE={metrics['rmse']:.2f} | "
                f"R2={metrics['r2']:.3f}"
            )

        if args.command == "predict":
            artifact = load_artifact()
            state = load_team_state()
            result = predict_game(
                artifact,
                state,
                home=args.home,
                away=args.away,
                as_of=args.date,
                line=args.line,
            )
            print(format_prediction(result))
    except (FileNotFoundError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    return 0

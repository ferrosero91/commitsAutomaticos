"""Interfaz de línea de comandos."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .app import Application, RunRequest
from .credentials import TokenProvider, build_default_token_store
from .errors import AutocommitError
from .logging_setup import configure_logging
from .settings import Settings
from .ui import ConsolePrompter, NonInteractivePrompter, Prompter, parse_collaborator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autocommit",
        description=(
            "Crea un historial de commits reproducible repartiendo los archivos "
            "del proyecto entre uno o varios colaboradores."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"autocommit {__version__}")

    parser.add_argument(
        "--repo",
        type=Path,
        help="Carpeta del proyecto. Si se omite, se abre un dialogo o se pregunta.",
    )
    parser.add_argument(
        "--remote-url",
        help="URL del remoto (sin credenciales). Si origin ya existe se conserva.",
    )
    parser.add_argument("--branch", help="Rama local a usar o crear.")
    parser.add_argument(
        "--collaborator",
        action="append",
        default=[],
        metavar="NOMBRE:USUARIO:EMAIL:COMMITS",
        help="Colaborador a incluir en el plan; se puede repetir.",
    )

    parser.add_argument(
        "--max-files-per-commit",
        type=int,
        default=100,
        metavar="N",
        help="Maximo de archivos por commit (también limita cada push).",
    )
    parser.add_argument(
        "--push-every",
        type=int,
        default=1,
        metavar="N",
        help="Commits entre pushes; 0 hace un único push al final.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        metavar="SEGUNDOS",
        help="Espera entre pushes.",
    )
    parser.add_argument(
        "--max-file-size-mb",
        type=float,
        default=10.0,
        metavar="MB",
        help="Tamano máximo por archivo.",
    )
    parser.add_argument(
        "--allow-secrets",
        action="store_true",
        help="No bloquear archivos con posibles secretos (no recomendado).",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostrar el plan sin modificar el repositorio.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continuar desde .git/autocommit/state.json.",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continuar con el siguiente commit si uno falla.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Semilla para que los mensajes generados sean reproducibles.",
    )

    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="No pedir confirmación antes de ejecutar.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="No solicitar datos por consola; falla si faltan argumentos.",
    )
    parser.add_argument("--log-file", type=Path, help="Archivo donde guardar el log.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Nivel de detalle en consola.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logger = configure_logging(args.log_level, args.log_file)

    interactive = not args.non_interactive and sys.stdin.isatty()
    prompter: Prompter = ConsolePrompter() if interactive else NonInteractivePrompter()

    try:
        settings = Settings(
            max_files_per_commit=args.max_files_per_commit,
            push_every=args.push_every,
            delay_seconds=args.delay,
            max_file_size_mb=args.max_file_size_mb,
            allow_secrets=args.allow_secrets,
            dry_run=args.dry_run,
            resume=args.resume,
            keep_going=args.keep_going,
            seed=args.seed,
        )
        collaborators = tuple(parse_collaborator(spec) for spec in args.collaborator)
        tokens = TokenProvider(
            build_default_token_store(),
            prompter,
            logger=logging.getLogger("autocommit.credentials"),
        )
        application = Application(settings, prompter, tokens, logger=logger)
        summary = application.run(
            RunRequest(
                repo_path=args.repo,
                remote_url=args.remote_url,
                branch=args.branch,
                collaborators=collaborators,
                assume_yes=args.yes,
                non_interactive=not interactive,
            )
        )
    except AutocommitError as exc:
        logger.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        logger.error("Interrumpido por el usuario.")
        return 130

    return 0 if summary.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

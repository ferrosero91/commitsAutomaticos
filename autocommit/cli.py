"""Interfaz de línea de comandos."""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .ai import (
    DEFAULT_API_KEY_ENV,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    AIConfig,
    AIMessageFactory,
    OpenCodeChatClient,
    default_key_file,
    resolve_api_key,
)
from .app import Application, RunRequest
from .credentials import TokenProvider, build_default_token_store
from .env import load_env_file
from .errors import AutocommitError
from .logging_setup import configure_logging
from .messages import CommitMessageFactory, ConventionalCommitMessageFactory
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
        "--gui",
        action="store_true",
        help="Abre la interfaz gráfica en lugar de la consola.",
    )

    parser.add_argument(
        "--repo",
        type=Path,
        help="Carpeta del proyecto. Si se omite, se abre un diálogo o se pregunta.",
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
        "--ai",
        action="store_true",
        help="Genera los mensajes con OpenCode; si falla, usa la heurística.",
    )
    parser.add_argument(
        "--ai-model",
        default=None,
        metavar="MODELO",
        help=(
            "Modelo de OpenCode para los mensajes "
            f"(env AUTOCOMMIT_AI_MODEL; por defecto {DEFAULT_MODEL})."
        ),
    )
    parser.add_argument(
        "--ai-base-url",
        default=None,
        metavar="URL",
        help=(
            "Base OpenAI-compatible (Go o pay-as-you-go) "
            f"(env AUTOCOMMIT_AI_BASE_URL; por defecto {DEFAULT_BASE_URL})."
        ),
    )
    parser.add_argument(
        "--ai-api-key-env",
        default=DEFAULT_API_KEY_ENV,
        metavar="VAR",
        help="Variable de entorno que contiene la clave de OpenCode.",
    )
    parser.add_argument(
        "--ai-api-key",
        metavar="CLAVE",
        help="Clave de OpenCode (insegura: queda en el historial del shell).",
    )
    parser.add_argument(
        "--ai-api-key-file",
        type=Path,
        default=default_key_file(),
        metavar="RUTA",
        help="Archivo con la clave; se guarda con permisos 0600 si se solicita.",
    )
    parser.add_argument(
        "--ai-timeout",
        type=float,
        default=30.0,
        metavar="SEGUNDOS",
        help="Timeout por llamada a la IA.",
    )
    parser.add_argument(
        "--ai-max-calls",
        type=int,
        default=200,
        metavar="N",
        help="Máximo de mensajes generados por IA; después se usa la heurística.",
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
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Carga variables CLAVE=VALOR antes de ejecutar (p. ej. OPENCODE_API_KEY).",
    )
    parser.add_argument("--log-file", type=Path, help="Archivo donde guardar el log.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Nivel de detalle en consola.",
    )
    return parser


def resolve_ai_config(
    args: argparse.Namespace,
    logger: logging.Logger,
    *,
    prompter: Prompter | None = None,
) -> AIConfig:
    """Resuelve clave, modelo y URL de la IA a partir de argumentos y entorno."""
    api_key = resolve_api_key(
        explicit=args.ai_api_key,
        env_var=args.ai_api_key_env,
        key_file=args.ai_api_key_file,
        prompter=prompter,
        logger=logger,
    )
    model = args.ai_model or os.environ.get("AUTOCOMMIT_AI_MODEL") or DEFAULT_MODEL
    base_url = args.ai_base_url or os.environ.get("AUTOCOMMIT_AI_BASE_URL") or DEFAULT_BASE_URL
    return AIConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=args.ai_timeout,
        max_calls=args.ai_max_calls,
    )


def build_message_factory(
    args: argparse.Namespace,
    settings: Settings,
    logger: logging.Logger,
    *,
    prompter: Prompter | None = None,
) -> CommitMessageFactory | None:
    """Construye el generador de mensajes con IA si se solicitó."""
    if not args.ai:
        return None

    config = resolve_ai_config(args, logger, prompter=prompter)
    logger.info("Mensajes con IA: %s en %s", config.model, config.base_url)

    rng = random.Random(settings.seed) if settings.seed is not None else None
    fallback = ConventionalCommitMessageFactory(rng)
    client = OpenCodeChatClient(config, logger=logger)
    return AIMessageFactory(client, fallback, max_calls=config.max_calls, logger=logger)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logger = configure_logging(args.log_level, args.log_file)

    if args.env_file:
        applied = load_env_file(args.env_file)
        logger.info(
            "Variables cargadas desde %s: %s",
            args.env_file,
            ", ".join(sorted(applied)) or "ninguna nueva",
        )

    if args.gui:
        try:
            from .gui import launch_gui
        except ImportError as exc:
            logger.error("La interfaz gráfica requiere tkinter: %s", exc)
            return 2
        return launch_gui()

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
        ai_logger = logging.getLogger("autocommit.ai")
        message_factory = build_message_factory(
            args,
            settings,
            ai_logger,
            prompter=prompter if interactive else None,
        )
        tokens = TokenProvider(
            build_default_token_store(),
            prompter,
            logger=logging.getLogger("autocommit.credentials"),
        )
        application = Application(
            settings,
            prompter,
            tokens,
            logger=logger,
            message_factory=message_factory,
        )
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

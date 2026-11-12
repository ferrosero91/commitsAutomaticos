"""Configuración de logging con redacción de credenciales."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .security import RedactingFilter

LOGGER_NAME = "autocommit"


def configure_logging(level: str = "INFO", log_file: Path | None = None) -> logging.Logger:
    """Configura el logger del paquete y devuelve el logger raiz."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(logging.Formatter("%(message)s"))
    console.addFilter(RedactingFilter())
    logger.addHandler(console)

    if log_file is not None:
        log_file = Path(log_file).expanduser()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        file_handler.addFilter(RedactingFilter())
        logger.addHandler(file_handler)

    return logger

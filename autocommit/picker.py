"""Selección de la carpeta del proyecto.

``tkinter`` es opcional: si no está disponible o falla (entornos sin pantalla),
se pide la ruta por consola.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .ui import Prompter


def select_project_folder(
    prompter: Prompter, *, logger: logging.Logger | None = None
) -> Path | None:
    log = logger or logging.getLogger("autocommit.picker")
    selected = _ask_with_dialog(log)
    if selected:
        return selected

    raw = prompter.text("Ruta del proyecto")
    return Path(raw).expanduser() if raw else None


def _ask_with_dialog(logger: logging.Logger) -> Path | None:
    try:
        import tkinter
        from tkinter import filedialog
    except Exception:  # pragma: no cover - depende del entorno
        logger.debug("tkinter no está disponible; se usará la consola.")
        return None

    try:
        root = tkinter.Tk()
        root.withdraw()
        try:
            selected = filedialog.askdirectory(title="Selecciona la carpeta del proyecto")
        finally:
            root.destroy()
    except Exception as exc:  # pragma: no cover - depende del entorno
        logger.debug("No se pudo abrir el dialogo de carpetas: %s", exc)
        return None

    return Path(selected).expanduser() if selected else None

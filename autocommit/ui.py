"""Interacción con el usuario.

``Prompter`` permite sustituir la consola por un doble de prueba y desacopla
la lógica de aplicación de ``input()``/``getpass``.
"""

from __future__ import annotations

import getpass
import time
from collections.abc import Sequence
from typing import Protocol

from .errors import ValidationError
from .models import Collaborator

TRUE_ANSWERS = frozenset({"s", "si", "sí", "y", "yes", "1"})
FALSE_ANSWERS = frozenset({"n", "no", "0"})

COLLABORATOR_FORMAT = "NOMBRE:USUARIO:EMAIL:COMMITS"


class Prompter(Protocol):
    def text(self, prompt: str, default: str | None = None) -> str: ...

    def secret(self, prompt: str) -> str: ...

    def integer(
        self, prompt: str, default: int | None = None, minimum: int | None = None
    ) -> int: ...

    def confirm(self, prompt: str, default: bool = False) -> bool: ...

    def select(
        self, prompt: str, options: Sequence[str], allow_empty: bool = False
    ) -> int | None: ...


class ConsolePrompter:
    """Implementación interactiva sobre stdin/stdout."""

    def text(self, prompt: str, default: str | None = None) -> str:
        suffix = f" [{default}]" if default else ""
        while True:
            value = input(f"{prompt}{suffix}: ").strip()
            if value:
                return value
            if default is not None:
                return default
            print("  Este valor es obligatorio.")

    def secret(self, prompt: str) -> str:
        return getpass.getpass(prompt)

    def integer(self, prompt: str, default: int | None = None, minimum: int | None = None) -> int:
        suffix = f" [{default}]" if default is not None else ""
        while True:
            raw = input(f"{prompt}{suffix}: ").strip()
            if not raw and default is not None:
                return default
            try:
                value = int(raw)
            except ValueError:
                print("  Introduce un número entero.")
                continue
            if minimum is not None and value < minimum:
                print(f"  El valor debe ser mayor o igual que {minimum}.")
                continue
            return value

    def confirm(self, prompt: str, default: bool = False) -> bool:
        hint = "[S/n]" if default else "[s/N]"
        while True:
            raw = input(f"{prompt} {hint}: ").strip().lower()
            if not raw:
                return default
            if raw in TRUE_ANSWERS:
                return True
            if raw in FALSE_ANSWERS:
                return False
            print("  Responde 's' o 'n'.")

    def select(self, prompt: str, options: Sequence[str], allow_empty: bool = False) -> int | None:
        for index, option in enumerate(options, start=1):
            print(f"  {index}. {option}")
        hint = " (vacio para crear una nueva)" if allow_empty else ""
        while True:
            raw = input(f"{prompt}{hint}: ").strip()
            if not raw:
                if allow_empty:
                    return None
                continue
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            print(f"  Elige un número entre 1 y {len(options)}.")


class NonInteractivePrompter:
    """Falla con un mensaje accionable en lugar de bloquearse sin TTY."""

    def text(self, prompt: str, default: str | None = None) -> str:
        if default is not None:
            return default
        raise ValidationError(
            f"Modo no interactivo: falta un valor para «{prompt}». Pasalo como argumento de la CLI."
        )

    def secret(self, prompt: str) -> str:
        raise ValidationError(
            "Modo no interactivo: no se puede solicitar un token. Define "
            "AUTOCOMMIT_TOKEN_<USUARIO> o guardalo previamente con una "
            "ejecución interactiva."
        )

    def integer(self, prompt: str, default: int | None = None, minimum: int | None = None) -> int:
        if default is not None:
            return default
        raise ValidationError(f"Modo no interactivo: falta un valor numerico para «{prompt}».")

    def confirm(self, prompt: str, default: bool = False) -> bool:
        return default

    def select(self, prompt: str, options: Sequence[str], allow_empty: bool = False) -> int | None:
        if allow_empty:
            return None
        raise ValidationError(f"Modo no interactivo: falta una selección para «{prompt}».")


def parse_collaborator(spec: str) -> Collaborator:
    """Convierte ``Nombre:usuario:email:commits`` en un colaborador."""
    parts = [part.strip() for part in (spec or "").split(":")]
    if len(parts) != 4:
        raise ValidationError(
            f"Formato de colaborador invalido: {spec!r}. Se esperaba {COLLABORATOR_FORMAT}."
        )
    name, username, email, commits_text = parts
    try:
        commits = int(commits_text)
    except ValueError as exc:
        raise ValidationError(
            f"La cantidad de commits de @{username} debe ser un número entero."
        ) from exc
    return Collaborator(name=name, username=username, email=email, commits=commits)


def countdown(seconds: float) -> None:
    """Espera mostrando una cuenta atras simple, sin dependencias externas."""
    if seconds <= 0:
        return
    if seconds < 1:
        time.sleep(seconds)
        return
    for remaining in range(int(round(seconds)), 0, -1):
        print(f"\r⏳ Esperando {remaining:>3}s...", end="", flush=True)
        time.sleep(1)
    print("\r" + " " * 32 + "\r", end="", flush=True)

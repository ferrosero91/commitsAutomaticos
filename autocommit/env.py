"""Carga explícita de variables desde un archivo ``.env``.

No hay dependencias: se aceptan líneas ``CLAVE=VALOR``, comentarios con ``#``
y el prefijo opcional ``export``. Nunca se registran los valores, solo los
nombres de las variables aplicadas.
"""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

from .errors import ValidationError

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env_file(path: Path) -> dict[str, str]:
    """Lee un archivo ``.env`` y devuelve sus pares clave/valor."""
    path = Path(path).expanduser()
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValidationError(f"No existe el archivo de variables {path}.") from exc
    except OSError as exc:
        raise ValidationError(f"No se pudo leer {path}: {exc}") from exc

    values: dict[str, str] = {}
    for number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise ValidationError(f"Línea {number} inválida en {path}: se esperaba CLAVE=VALOR.")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not _KEY_RE.match(key):
            raise ValidationError(f"Línea {number} inválida en {path}: nombre de variable {key!r}.")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(
    path: Path,
    environ: MutableMapping[str, str] | None = None,
    *,
    override: bool = False,
) -> dict[str, str]:
    """Aplica un ``.env`` al entorno y devuelve solo lo que cambió.

    Por defecto no pisa variables ya definidas: así una exportación explícita
    o un secreto de CI siempre tiene prioridad sobre el archivo.
    """
    target = environ if environ is not None else os.environ
    values = parse_env_file(path)
    applied: dict[str, str] = {}
    for key, value in values.items():
        if override or not target.get(key):
            target[key] = value
            applied[key] = value
    return applied

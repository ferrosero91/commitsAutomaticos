"""Ejecución de comandos externos sin ``shell=True``.

Encapsula ``subprocess`` para que el resto del código no dependa de detalles
del sistema operativo y para garantizar que los errores se redacten antes de
llegar a un log.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import GitError
from .security import redact


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner:
    """Ejecuta comandos en un directorio de trabajo concreto."""

    def __init__(
        self,
        cwd: Path,
        *,
        timeout: float | None = 120,
        redactor: Callable[[str], str] = redact,
    ) -> None:
        self.cwd = Path(cwd)
        self._timeout = timeout
        self._redact = redactor

    def run(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        stdin: str | None = None,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult:
        command = [str(arg) for arg in args]
        try:
            completed = subprocess.run(
                command,
                cwd=self.cwd,
                env=dict(env) if env is not None else None,
                input=stdin,
                text=True,
                capture_output=True,
                timeout=timeout if timeout is not None else self._timeout,
            )
        except FileNotFoundError as exc:
            raise GitError(
                f"No se encontró el ejecutable {command[0]!r}. "
                "Instala git y asegúrate de que esté en el PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(
                f"El comando {command[0]!r} excedió el tiempo máximo de espera."
            ) from exc

        result = CommandResult(
            tuple(command),
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )

        if check and not result.ok:
            detail = self._redact(result.stderr.strip()) or "sin salida de error"
            raise GitError(
                f"Falló el comando {' '.join(self._redact(arg) for arg in command[:3])} "
                f"(código {result.returncode})",
                returncode=result.returncode,
                stderr=detail,
            )

        return result

    def git(self, *args: str, **kwargs: object) -> CommandResult:
        return self.run(("git", *args), **kwargs)  # type: ignore[arg-type]

"""Excepciones propias del dominio.

Todas heredan de :class:`AutocommitError` para que la CLI pueda distinguir los
errores esperados (entrada invalida, git, estado corrupto) de un fallo
inesperado de Python.
"""

from __future__ import annotations


class AutocommitError(Exception):
    """Error esperado y legible por el usuario."""


class ValidationError(AutocommitError):
    """Entrada del usuario o configuración del plan invalida."""


class SecurityError(AutocommitError):
    """Contenido rechazado por el validador de seguridad."""


class StateError(AutocommitError):
    """El estado persistido no se puede leer o es incompatible."""


class AIError(AutocommitError):
    """El proveedor de mensajes con IA no respondió o devolvió algo inválido."""


class OperationCancelled(AutocommitError):
    """El usuario solicitó detener la ejecución."""


class GitError(AutocommitError):
    """Un comando de git terminó con un código de salida distinto de cero."""

    def __init__(self, message: str, *, returncode: int | None = None, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr

    def __str__(self) -> str:
        message = str(self.args[0]) if self.args else "error de git"
        if self.stderr and self.stderr not in message:
            return f"{message}: {self.stderr}"
        return message

"""Configuración inmutable de una ejecución."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .errors import ValidationError

MEGABYTE = 1024 * 1024


@dataclass(frozen=True)
class Settings:
    """Parametros que afectan al plan y a la ejecución.

    Los valores por defecto son conservadores: un push por commit, sin esperas
    artificiales y con un limite de 10 MB por archivo.
    """

    max_files_per_commit: int = 100
    push_every: int = 1
    delay_seconds: float = 0.0
    spread_days: float = 0.0
    interleave: bool = False
    max_file_size_mb: float = 10.0
    allow_secrets: bool = False
    dry_run: bool = False
    resume: bool = False
    keep_going: bool = False
    seed: int | None = None

    def __post_init__(self) -> None:
        if self.max_files_per_commit < 1:
            raise ValidationError("max_files_per_commit debe ser mayor o igual que 1.")
        if self.push_every < 0:
            raise ValidationError("push_every no puede ser negativo (0 = push único al final).")
        if not math.isfinite(self.delay_seconds) or self.delay_seconds < 0:
            raise ValidationError("delay_seconds debe ser un número finito y no negativo.")
        if not math.isfinite(self.spread_days) or self.spread_days < 0:
            raise ValidationError("spread_days debe ser un número finito y no negativo.")
        if not math.isfinite(self.max_file_size_mb) or self.max_file_size_mb <= 0:
            raise ValidationError("max_file_size_mb debe ser mayor que 0.")
        if self.seed is not None and self.seed < 0:
            raise ValidationError("La semilla no puede ser negativa.")

    @property
    def max_file_size_bytes(self) -> int:
        return int(self.max_file_size_mb * MEGABYTE)

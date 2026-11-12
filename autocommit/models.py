"""Modelos del dominio, sin dependencias de infraestructura.

Todos son inmutables y validan sus invariantes al construirse, de modo que un
objeto invalido no puede circular por el resto del programa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ValidationError

_USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class Collaborator:
    """Persona a la que se atribuiran commits concretos."""

    name: str
    username: str
    email: str
    commits: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", (self.name or "").strip())
        object.__setattr__(self, "username", (self.username or "").strip())
        object.__setattr__(self, "email", (self.email or "").strip())

        if not self.name:
            raise ValidationError("El nombre del colaborador no puede estar vacio.")
        if not _USERNAME_RE.match(self.username):
            raise ValidationError(
                f"El usuario de GitHub {self.username!r} no es valido "
                "(letras, numeros y guiones; máximo 39 caracteres)."
            )
        if not _EMAIL_RE.match(self.email):
            raise ValidationError(f"El email {self.email!r} no es valido.")
        if isinstance(self.commits, bool) or not isinstance(self.commits, int):
            raise ValidationError("La cantidad de commits debe ser un número entero.")
        if self.commits < 0:
            raise ValidationError("La cantidad de commits no puede ser negativa.")

    @property
    def identity(self) -> tuple[str, str]:
        """Par (nombre, email) listo para `git config`."""
        return self.name, self.email


@dataclass(frozen=True)
class PlannedCommit:
    """Un commit concreto dentro del plan."""

    index: int
    collaborator: Collaborator
    files: tuple[str, ...]
    message: str

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValidationError("El índice del commit no puede ser negativo.")
        if not self.files:
            raise ValidationError("Un commit planificado debe incluir al menos un archivo.")
        if not self.message.strip():
            raise ValidationError("El mensaje del commit no puede estar vacio.")


@dataclass(frozen=True)
class CommitPlan:
    """Plan completo e inmutable de la ejecución."""

    branch: str
    commits: tuple[PlannedCommit, ...]
    total_files: int

    def __post_init__(self) -> None:
        if not self.branch.strip():
            raise ValidationError("La rama del plan no puede estar vacia.")
        indices = [commit.index for commit in self.commits]
        if len(indices) != len(set(indices)):
            raise ValidationError("El plan contiene indices de commit duplicados.")
        if any(index >= len(self.commits) for index in indices):
            raise ValidationError("El plan contiene indices de commit fuera de rango.")

    def __len__(self) -> int:
        return len(self.commits)

    @property
    def total_commits(self) -> int:
        return len(self.commits)

    @property
    def collaborator_count(self) -> int:
        return len(self.collaborators)

    @property
    def collaborators(self) -> tuple[Collaborator, ...]:
        """Colaboradores unicos en el orden en que aparecen."""
        unique: list[Collaborator] = []
        for commit in self.commits:
            if commit.collaborator not in unique:
                unique.append(commit.collaborator)
        return tuple(unique)

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(path for commit in self.commits for path in commit.files)

    def pending(self, completed: frozenset[int] | set[int]) -> tuple[PlannedCommit, ...]:
        return tuple(commit for commit in self.commits if commit.index not in completed)

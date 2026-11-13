"""Reparto de archivos en commits.

El planificador es puro: recibe archivos, colaboradores y una rama, y devuelve
un :class:`CommitPlan` o lanza ``ValidationError``. No toca el repositorio.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from .errors import ValidationError
from .messages import CommitMessageFactory
from .models import Collaborator, CommitPlan, PlannedCommit
from .settings import Settings


class CommitPlanner:
    """Distribuye archivos en commits equilibrados y acotados."""

    def __init__(self, settings: Settings, message_factory: CommitMessageFactory) -> None:
        self._settings = settings
        self._messages = message_factory

    @staticmethod
    def required_commits(total_files: int, max_files_per_commit: int) -> int:
        if max_files_per_commit < 1:
            raise ValidationError("El máximo de archivos por commit debe ser mayor que 0.")
        return max(1, math.ceil(total_files / max_files_per_commit))

    def build(
        self,
        files: Sequence[str],
        collaborators: Sequence[Collaborator],
        branch: str,
    ) -> CommitPlan:
        branch = (branch or "").strip()
        if not branch:
            raise ValidationError("Debes indicar una rama.")

        paths = list(dict.fromkeys(files))
        if not paths:
            raise ValidationError("No hay archivos para repartir en commits.")
        if not collaborators:
            raise ValidationError("Debes indicar al menos un colaborador.")

        total_commits = sum(collaborator.commits for collaborator in collaborators)
        if total_commits < 1:
            raise ValidationError("El total de commits debe ser al menos 1.")

        if total_commits > len(paths):
            raise ValidationError(
                f"Solicitaste {total_commits} commits para {len(paths)} archivos. "
                "Cada commit necesita al menos un archivo: reduce el número de commits "
                "o añade más archivos."
            )

        required = self.required_commits(len(paths), self._settings.max_files_per_commit)
        if total_commits < required:
            raise ValidationError(
                f"Con un máximo de {self._settings.max_files_per_commit} archivos por commit, "
                f"{len(paths)} archivos requieren al menos {required} commits "
                f"(solicitaste {total_commits}). Aumenta los commits o "
                "--max-files-per-commit."
            )

        sizes = self._distribute(len(paths), total_commits)
        planned: list[PlannedCommit] = []
        cursor = 0

        for index, collaborator in enumerate(self._author_order(collaborators)):
            size = sizes[index]
            chunk = tuple(paths[cursor : cursor + size])
            cursor += size
            planned.append(
                PlannedCommit(
                    index=index,
                    collaborator=collaborator,
                    files=chunk,
                    message=self._messages.create(chunk),
                )
            )

        return CommitPlan(branch=branch, commits=tuple(planned), total_files=len(paths))

    def _author_order(self, collaborators: Sequence[Collaborator]) -> list[Collaborator]:
        if not self._settings.interleave:
            return [
                collaborator for collaborator in collaborators for _ in range(collaborator.commits)
            ]

        remaining = [[collaborator, collaborator.commits] for collaborator in collaborators]
        order: list[Collaborator] = []
        while any(count for _, count in remaining):
            for entry in remaining:
                if entry[1] > 0:
                    order.append(entry[0])
                    entry[1] -= 1
        return order

    @staticmethod
    def _distribute(total_files: int, total_commits: int) -> list[int]:
        base, remainder = divmod(total_files, total_commits)
        return [base + (1 if index < remainder else 0) for index in range(total_commits)]

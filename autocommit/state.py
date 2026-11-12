"""Persistencia del progreso para poder reanudar una ejecución.

El estado vive en ``.git/autocommit/state.json`` (nunca se versiona) y no
contiene tokens: solo el plan y los indices ya completados.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .errors import StateError
from .models import Collaborator, CommitPlan, PlannedCommit

STATE_VERSION = 1
STATE_FILENAME = "state.json"
STATE_DIR_NAME = "autocommit"


@dataclass(frozen=True)
class JobState:
    plan: CommitPlan
    completed: frozenset[int]
    pushed: int = -1

    def pending(self) -> tuple[PlannedCommit, ...]:
        return self.plan.pending(self.completed)

    @property
    def is_complete(self) -> bool:
        return len(self.completed) >= self.plan.total_commits

    @property
    def has_unpushed_commits(self) -> bool:
        highest = max(self.completed, default=-1)
        return self.pushed < highest


class StateStore:
    """Lectura y escritura atomica del estado de la ejecución."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @classmethod
    def in_git_dir(cls, git_dir: Path) -> StateStore:
        return cls(Path(git_dir) / STATE_DIR_NAME / STATE_FILENAME)

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> JobState | None:
        if not self.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"No se pudo leer el estado {self.path}: {exc}") from exc

        if payload.get("version") != STATE_VERSION:
            raise StateError(
                f"El estado {self.path} usa una versión distinta de {STATE_VERSION}; "
                "eliminalo o ejecuta sin --resume."
            )

        try:
            collaborators = {
                entry["username"]: Collaborator(
                    name=entry["name"],
                    username=entry["username"],
                    email=entry["email"],
                    commits=entry["commits"],
                )
                for entry in payload["collaborators"]
            }
            commits = tuple(
                PlannedCommit(
                    index=entry["index"],
                    collaborator=collaborators[entry["username"]],
                    files=tuple(entry["files"]),
                    message=entry["message"],
                )
                for entry in payload["commits"]
            )
            plan = CommitPlan(
                branch=payload["branch"],
                commits=commits,
                total_files=payload["total_files"],
            )
            indices = {commit.index for commit in plan.commits}
            completed = frozenset(
                index for index in payload.get("completed", []) if index in indices
            )
            pushed = int(payload.get("pushed", -1))
            pushed = max(-1, min(pushed, plan.total_commits - 1))
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError(f"El estado {self.path} está mal formado: {exc}") from exc

        return JobState(plan=plan, completed=completed, pushed=pushed)

    def save(self, plan: CommitPlan, completed: Iterable[int], pushed: int = -1) -> None:
        collaborators = [
            {
                "name": collaborator.name,
                "username": collaborator.username,
                "email": collaborator.email,
                "commits": collaborator.commits,
            }
            for collaborator in plan.collaborators
        ]
        payload = {
            "version": STATE_VERSION,
            "branch": plan.branch,
            "total_files": plan.total_files,
            "collaborators": collaborators,
            "completed": sorted(set(completed)),
            "pushed": pushed,
            "commits": [
                {
                    "index": commit.index,
                    "username": commit.collaborator.username,
                    "files": list(commit.files),
                    "message": commit.message,
                }
                for commit in plan.commits
            ],
        }
        self._write(payload)

    def clear(self) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink(missing_ok=True)

    def _write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".state-", suffix=".json"
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink(missing_ok=True)

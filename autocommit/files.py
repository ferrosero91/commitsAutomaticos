"""Descubrimiento de archivos candidatos.

El filtrado de ``.gitignore`` se delega en ``git check-ignore``: usar la propia
implementación de git evita reimplementar (mal) la semantica de patrones,
negaciones y ``.gitignore`` anidados.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import GitError
from .runner import CommandRunner

DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "virtualenv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".next",
        ".nuxt",
        "dist",
        "build",
        "target",
        "coverage",
        ".idea",
        ".vscode",
    }
)

DEFAULT_EXCLUDED_SUFFIXES = frozenset(
    {".tmp", ".log", ".bak", ".pyc", ".pyo", ".swp", ".swo", ".orig"}
)

DEFAULT_EXCLUDED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


@dataclass(frozen=True)
class DiscoveryReport:
    files: tuple[str, ...]
    ignored: tuple[str, ...]


class GitIgnoreFilter:
    """Consulta a git que rutas estan ignoradas por los ``.gitignore``."""

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def ignored(self, paths: Sequence[str]) -> set[str]:
        if not paths:
            return set()

        payload = "\0".join(paths) + "\0"
        # --no-index aplica las reglas también a archivos ya rastreados: un
        # archivo que coincide con .gitignore nunca debe entrar en el plan.
        result = self._runner.git(
            "check-ignore", "--no-index", "--stdin", "-z", stdin=payload, check=False
        )
        if result.returncode not in (0, 1):
            raise GitError(
                "No se pudo evaluar .gitignore con 'git check-ignore'.",
                returncode=result.returncode,
                stderr=result.stderr.strip(),
            )
        if result.returncode == 1:
            return set()
        return {entry for entry in result.stdout.split("\0") if entry}


class FileDiscovery:
    """Recorre el proyecto y devuelve rutas relativas ordenadas."""

    def __init__(
        self,
        root: Path,
        ignore_filter: GitIgnoreFilter | None = None,
        *,
        excluded_dirs: frozenset[str] = DEFAULT_EXCLUDED_DIRS,
        excluded_suffixes: frozenset[str] = DEFAULT_EXCLUDED_SUFFIXES,
        excluded_names: frozenset[str] = DEFAULT_EXCLUDED_NAMES,
    ) -> None:
        self._root = Path(root).resolve()
        self._ignore_filter = ignore_filter
        self._excluded_dirs = excluded_dirs
        self._excluded_suffixes = excluded_suffixes
        self._excluded_names = excluded_names

    def discover(self) -> DiscoveryReport:
        candidates = self._walk()
        if self._ignore_filter is not None:
            ignored = self._ignore_filter.ignored(candidates)
        else:
            ignored = set()
        files = tuple(sorted(path for path in candidates if path not in ignored))
        return DiscoveryReport(files=files, ignored=tuple(sorted(ignored)))

    def _walk(self) -> list[str]:
        candidates: list[str] = []
        for current, directories, filenames in os.walk(self._root):
            directories[:] = sorted(
                name
                for name in directories
                if name not in self._excluded_dirs
                and not name.endswith(".git")
                and not os.path.islink(os.path.join(current, name))
                and not (Path(current) / name / ".git").exists()
            )
            for filename in filenames:
                if filename in self._excluded_names:
                    continue
                if Path(filename).suffix.lower() in self._excluded_suffixes:
                    continue
                absolute = Path(current) / filename
                if not absolute.is_file():
                    continue
                candidates.append(absolute.relative_to(self._root).as_posix())
        return candidates

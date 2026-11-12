"""Generación de mensajes de commit.

El formato sigue Conventional Commits (``tipo(scope): descripción``) para que
el historial sea legible y filtrable. La heuristica se basa en las extensiones
y rutas de los archivos del grupo.
"""

from __future__ import annotations

import random
import re
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Protocol

MAX_SUBJECT_LENGTH = 72

DOC_SUFFIXES = frozenset({".md", ".rst", ".txt", ".adoc"})
STYLE_SUFFIXES = frozenset({".css", ".scss", ".sass", ".less", ".styl"})
UI_SUFFIXES = frozenset({".tsx", ".jsx", ".vue", ".svelte", ".html", ".htm"})
CONFIG_SUFFIXES = frozenset(
    {
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".xml",
        ".properties",
    }
)
CODE_SUFFIXES = frozenset(
    {
        ".py",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".java",
        ".kt",
        ".kts",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".cs",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".swift",
        ".scala",
        ".sh",
        ".bash",
        ".zsh",
        ".sql",
        ".graphql",
        ".proto",
    }
)

TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__", "spec", "specs"})
TEST_NAME_RE = re.compile(r"(^test_|_test\.|\.test\.|\.spec\.|_spec\.|Test\.java$|Tests?\.java$)")

CONFIG_FILE_NAMES = frozenset(
    {
        ".editorconfig",
        ".gitignore",
        ".gitattributes",
        ".dockerignore",
        "dockerfile",
        "makefile",
        "justfile",
        "procfile",
        "requirements.txt",
    }
)

MULTI_FILE_SUBJECTS: dict[str, tuple[str, ...]] = {
    "feat": (
        "añadir nueva funcionalidad",
        "implementar mejoras",
        "actualizar la implementación",
        "ampliar la funcionalidad existente",
        "integrar nuevos cambios",
    ),
    "test": (
        "actualizar las pruebas",
        "ampliar la cobertura",
        "corregir casos de prueba",
        "refactorizar las pruebas",
        "añadir nuevos casos",
    ),
    "docs": (
        "actualizar la documentación",
        "mejorar las guías",
        "corregir la documentación",
        "ampliar los ejemplos",
        "revisar las instrucciones",
    ),
    "style": (
        "ajustar los estilos",
        "mejorar el diseño visual",
        "corregir la maquetación",
        "actualizar las hojas de estilo",
        "afinar el responsive",
    ),
    "chore": (
        "actualizar la configuración",
        "ajustar las dependencias",
        "ordenar los archivos del proyecto",
        "actualizar las herramientas",
        "limpiar el proyecto",
    ),
    "fix": (
        "corregir errores",
        "resolver incidencias",
        "subsanar fallos",
        "corregir comportamientos inesperados",
    ),
}

SINGLE_FILE_VERBS: dict[str, tuple[str, ...]] = {
    "feat": ("añadir", "crear", "implementar"),
    "test": ("añadir pruebas de", "actualizar pruebas de"),
    "docs": ("documentar", "actualizar"),
    "style": ("actualizar estilos de", "ajustar"),
    "chore": ("actualizar", "ajustar"),
    "fix": ("corregir",),
}


def classify(path: str) -> str:
    """Devuelve el tipo convencional más probable para un archivo."""
    pure = PurePosixPath(path)
    name = pure.name
    lowered = name.lower()
    suffix = pure.suffix.lower()
    parent_parts = {part.lower() for part in pure.parts[:-1]}

    if parent_parts & TEST_DIR_NAMES or TEST_NAME_RE.search(name):
        return "test"
    if (
        lowered in {"readme", "license", "changelog", "contributing"}
        or suffix in DOC_SUFFIXES
        or "docs" in parent_parts
    ):
        return "docs"
    if suffix in STYLE_SUFFIXES:
        return "style"
    if suffix in CONFIG_SUFFIXES or lowered in CONFIG_FILE_NAMES:
        return "chore"
    if suffix in UI_SUFFIXES or suffix in CODE_SUFFIXES:
        return "feat"
    return "chore"


def _slug(value: str) -> str | None:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or None


class CommitMessageFactory(Protocol):
    def create(self, files: Sequence[str]) -> str:
        """Genera un mensaje para un grupo de archivos."""


class ConventionalCommitMessageFactory:
    """Crea mensajes ``tipo(scope): descripción`` de forma reproducible."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()

    def create(self, files: Sequence[str]) -> str:
        if not files:
            raise ValueError("Se necesita al menos un archivo para generar un mensaje.")

        kind = self._dominant_type(files)
        scope = self._scope(files)
        subject = self._subject(files, kind)
        return self._compose(kind, scope, subject)

    @staticmethod
    def _dominant_type(files: Sequence[str]) -> str:
        types = [classify(path) for path in files]
        unique = set(types)
        if len(unique) == 1:
            return types[0]
        if "feat" in unique:
            return "feat"
        counts = {kind: types.count(kind) for kind in unique}
        priority = ("feat", "test", "docs", "style", "chore", "fix")

        def order(kind: str) -> int:
            return priority.index(kind) if kind in priority else len(priority)

        return max(counts, key=lambda kind: (counts[kind], -order(kind)))

    @staticmethod
    def _scope(files: Sequence[str]) -> str | None:
        parents = {PurePosixPath(path).parent for path in files}
        if len(parents) == 1:
            parent = parents.pop()
            if str(parent) in (".", ""):
                return None
            return _slug(parent.name)

        segmented = [PurePosixPath(path).parts[:-1] for path in files]
        common: list[str] = []
        for parts in zip(*segmented):
            if len(set(parts)) != 1:
                break
            common.append(parts[0])
        if common:
            return _slug(common[-1])
        return None

    def _subject(self, files: Sequence[str], kind: str) -> str:
        if len(files) == 1:
            stem = PurePosixPath(files[0]).stem
            verbs = SINGLE_FILE_VERBS.get(kind, ("actualizar",))
            subject = f"{self._rng.choice(verbs)} {stem}"
        else:
            options = MULTI_FILE_SUBJECTS.get(kind, MULTI_FILE_SUBJECTS["chore"])
            subject = self._rng.choice(options)
        return subject.rstrip(".")

    @staticmethod
    def _compose(kind: str, scope: str | None, subject: str) -> str:
        if scope:
            candidate = f"{kind}({scope}): {subject}"
            if len(candidate) <= MAX_SUBJECT_LENGTH:
                return candidate

        candidate = f"{kind}: {subject}"
        if len(candidate) <= MAX_SUBJECT_LENGTH:
            return candidate

        available = MAX_SUBJECT_LENGTH - len(f"{kind}: ") - 3
        trimmed = subject[:available].rstrip(" ,.;")
        return f"{kind}: {trimmed}..."

"""Utilidades compartidas por las pruebas."""

from __future__ import annotations

import logging
import subprocess
import tempfile
import unittest
from pathlib import Path


def null_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f"autocommit.tests.{name}")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    return logger


class TemporaryProjectTestCase(unittest.TestCase):
    """Crea un directorio temporal por prueba."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="autocommit-tests-")
        self.addCleanup(self._temporary.cleanup)
        self.tmp = Path(self._temporary.name).resolve()

    def write(self, relative: str, content: str = "", encoding: str = "utf-8") -> Path:
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=encoding)
        return path

    def git(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.tmp),
            text=True,
            capture_output=True,
        )

    def init_repo(self) -> Path:
        result = self.git("init", "-q")
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.tmp

    def init_bare_remote(self, name: str = "remote.git") -> Path:
        path = self.tmp / name
        result = subprocess.run(
            ["git", "init", "--bare", "-q", str(path)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return path

    def git_stdout(self, *args: str, cwd: Path | None = None) -> str:
        result = self.git(*args, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout


class ScriptedPrompter:
    """Doble de prueba del protocolo ``Prompter``."""

    def __init__(
        self,
        texts: list[str] | None = None,
        secrets: list[str] | None = None,
        integers: list[int] | None = None,
        confirmations: list[bool] | None = None,
        selections: list[int | None] | None = None,
    ) -> None:
        self.texts = list(texts or [])
        self.secrets = list(secrets or [])
        self.integers = list(integers or [])
        self.confirmations = list(confirmations or [])
        self.selections = list(selections or [])

    def text(self, prompt: str, default: str | None = None) -> str:
        if self.texts:
            return self.texts.pop(0)
        if default is not None:
            return default
        raise AssertionError(f"Sin respuesta de texto para {prompt!r}")

    def secret(self, prompt: str) -> str:
        if not self.secrets:
            raise AssertionError(f"Sin token preparado para {prompt!r}")
        return self.secrets.pop(0)

    def integer(self, prompt: str, default: int | None = None, minimum: int | None = None) -> int:
        if self.integers:
            return self.integers.pop(0)
        if default is not None:
            return default
        raise AssertionError(f"Sin respuesta numerica para {prompt!r}")

    def confirm(self, prompt: str, default: bool = False) -> bool:
        if self.confirmations:
            return self.confirmations.pop(0)
        return default

    def select(self, prompt: str, options: list[str], allow_empty: bool = False) -> int | None:
        if self.selections:
            return self.selections.pop(0)
        return None

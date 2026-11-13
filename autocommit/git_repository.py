"""Adaptador del repositorio Git.

Todo el contacto con el binario ``git`` pasa por esta clase. Las credenciales
se entregan al push mediante un script ``GIT_ASKPASS`` temporal; nunca se
escriben en ``.git/config``, ni en la línea de comandos, ni en el entorno del
proceso padre.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .errors import GitError, ValidationError
from .runner import CommandRunner

REMOTE_NAME = "origin"
IDENTITY_KEYS = ("user.name", "user.email")
ASKPASS_TIMEOUT = 300
_CHUNK_SIZE = 100

_ASKPASS_SCRIPT = """#!/bin/sh
# Generado en tiempo de ejecución por autocommit. No se persiste.
case "$1" in
    *[Uu]sername*) printf '%s\\n' "${AUTOCOMMIT_ASKPASS_USERNAME:-x-access-token}" ;;
    *) cat "${AUTOCOMMIT_ASKPASS_TOKEN_FILE}" ;;
esac
"""

_URL_CREDENTIALS_RE = re.compile(r"^(https?://)(?:[^/@]+@)(.+)$")


@dataclass(frozen=True)
class Identity:
    name: str
    email: str


@dataclass(frozen=True)
class PushCredentials:
    username: str
    token: str


def strip_credentials(url: str) -> str:
    """Elimina ``usuario:token@`` de una URL HTTP(S)."""
    return _URL_CREDENTIALS_RE.sub(r"\1\2", (url or "").strip())


def normalize_remote_url(url: str) -> str:
    """Normaliza y valida la URL del remoto, descartando credenciales."""
    cleaned = strip_credentials(url)
    if not cleaned:
        raise ValidationError("La URL del remoto no puede estar vacia.")
    if any(character.isspace() for character in cleaned):
        raise ValidationError("La URL del remoto no puede contener espacios.")
    if "://" in cleaned:
        scheme = cleaned.split("://", 1)[0].lower()
        if scheme not in {"http", "https", "ssh", "git", "file"}:
            raise ValidationError(f"Esquema de URL no soportado: {scheme!r}.")
    return cleaned


class GitRepository:
    """Operaciones de Git necesarias para el flujo de autocommit."""

    def __init__(
        self,
        root: Path,
        runner: CommandRunner | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self._logger = logger or logging.getLogger("autocommit.git")
        self._runner = runner or CommandRunner(self.root)

    # ------------------------------------------------------------------ setup

    def is_initialized(self) -> bool:
        if not self.root.exists():
            return False
        result = self._runner.git("rev-parse", "--git-dir", check=False)
        return result.returncode == 0

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._runner.git("init", "-q")

    @property
    def git_dir(self) -> Path:
        result = self._runner.git("rev-parse", "--absolute-git-dir")
        return Path(result.stdout.strip())

    # ----------------------------------------------------------------- remotes

    def remote_url(self, name: str = REMOTE_NAME) -> str | None:
        result = self._runner.git("remote", "get-url", name, check=False)
        return result.stdout.strip() or None if result.returncode == 0 else None

    def add_remote(self, name: str, url: str) -> None:
        if self.remote_url(name) is not None:
            raise ValidationError(f"El remoto {name!r} ya existe.")
        self._runner.git("remote", "add", name, url)

    def set_remote_url(self, name: str, url: str) -> None:
        self._runner.git("remote", "set-url", name, url)

    def fetch(self, name: str = REMOTE_NAME) -> bool:
        """Actualiza referencias; no falla si no hay red."""
        if self.remote_url(name) is None:
            return False
        result = self._runner.git("fetch", "--prune", name, check=False, timeout=60)
        if not result.ok:
            self._logger.warning(
                "No se pudo actualizar el remoto %s (se continua sin fetch).", name
            )
            return False
        return True

    # ----------------------------------------------------------------- branches

    def branches(self) -> list[str]:
        result = self._runner.git("for-each-ref", "--format=%(refname:short)", "refs/heads")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def remote_branches(self, name: str = REMOTE_NAME) -> list[str]:
        result = self._runner.git(
            "for-each-ref", "--format=%(refname:short)", f"refs/remotes/{name}"
        )
        prefix = f"{name}/"
        branches = []
        for line in result.stdout.splitlines():
            ref = line.strip()
            if ref.startswith(prefix) and not ref.endswith("/HEAD"):
                branches.append(ref[len(prefix) :])
        return branches

    def current_branch(self) -> str | None:
        result = self._runner.git("symbolic-ref", "--short", "-q", "HEAD", check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def has_commits(self) -> bool:
        result = self._runner.git("rev-parse", "--verify", "--quiet", "HEAD", check=False)
        return result.returncode == 0

    def _ref_exists(self, ref: str) -> bool:
        result = self._runner.git("rev-parse", "--verify", "--quiet", ref, check=False)
        return result.returncode == 0

    def ensure_branch(self, branch: str, remote: str = REMOTE_NAME) -> None:
        """Deja el repositorio en ``branch`` sin perder trabajo existente."""
        branch = (branch or "").strip()
        if not branch:
            raise ValidationError("El nombre de la rama no puede estar vacio.")

        if self.current_branch() == branch:
            return
        if self._ref_exists(f"refs/heads/{branch}"):
            self._runner.git("checkout", branch)
            return
        if self._ref_exists(f"refs/remotes/{remote}/{branch}"):
            self._runner.git("checkout", "-b", branch, "--track", f"{remote}/{branch}")
            return
        if not self.has_commits():
            # Repositorio sin commits: HEAD aún es una rama no nacida.
            self._runner.git("symbolic-ref", "HEAD", f"refs/heads/{branch}")
            return
        self._runner.git("checkout", "-b", branch)

    # ---------------------------------------------------------------- identity

    def local_config(self, key: str) -> str | None:
        result = self._runner.git("config", "--local", "--get", key, check=False)
        if result.returncode == 0:
            return result.stdout.strip() or None
        if result.returncode == 1:
            return None
        raise GitError(
            f"No se pudo leer la configuración local {key!r}.",
            returncode=result.returncode,
            stderr=result.stderr.strip(),
        )

    def set_local_config(self, key: str, value: str) -> None:
        self._runner.git("config", "--local", key, value)

    def unset_local_config(self, key: str) -> None:
        self._runner.git("config", "--local", "--unset-all", key, check=False)

    def identity(self) -> Identity | None:
        name = self.local_config("user.name")
        email = self.local_config("user.email")
        if name is None or email is None:
            return None
        return Identity(name, email)

    def capture_identity(self) -> dict[str, str | None]:
        return {key: self.local_config(key) for key in IDENTITY_KEYS}

    def restore_identity(self, snapshot: Mapping[str, str | None]) -> None:
        for key, value in snapshot.items():
            if value is None:
                self.unset_local_config(key)
            else:
                self.set_local_config(key, value)

    def set_identity(self, identity: Identity | None) -> None:
        if identity is None:
            for key in IDENTITY_KEYS:
                self.unset_local_config(key)
            return
        self.set_local_config("user.name", identity.name)
        self.set_local_config("user.email", identity.email)

    # ----------------------------------------------------------------- staging

    def stage(self, files: Sequence[str]) -> list[str]:
        """Prepara los archivos que aún existen y devuelve la lista usada.

        Entre el escaneo y el commit pueden desaparecer archivos (builds,
        editores, procesos externos). Omitirlos evita abortar toda la
        ejecución por un pathspec que ya no existe.
        """
        if not files:
            raise ValidationError("No hay archivos que preparar para el commit.")

        existing: list[str] = []
        missing: list[str] = []
        for relative in files:
            if (self.root / relative).exists():
                existing.append(relative)
            else:
                missing.append(relative)

        if missing:
            preview = ", ".join(missing[:5])
            if len(missing) > 5:
                preview += f" y {len(missing) - 5} más"
            self._logger.warning(
                "Se omiten %d archivo(s) que ya no existen: %s",
                len(missing),
                preview,
            )

        if not existing:
            return []

        payload = "\0".join(existing) + "\0"
        try:
            self._runner.git("add", "--pathspec-from-file=-", "--pathspec-file-nul", stdin=payload)
        except GitError as exc:
            if not self._pathspec_option_unsupported(exc):
                raise
            # Compatibilidad con git < 2.25.
            for start in range(0, len(existing), _CHUNK_SIZE):
                chunk = existing[start : start + _CHUNK_SIZE]
                self._runner.git("add", "--", *chunk)

        return existing

    @staticmethod
    def _pathspec_option_unsupported(error: GitError) -> bool:
        message = f"{error.stderr} {error}".lower()
        return "pathspec-from-file" in message and (
            "unknown option" in message or "unrecognized option" in message
        )

    def has_staged_changes(self) -> bool:
        result = self._runner.git("diff", "--cached", "--quiet", check=False)
        if result.returncode == 0:
            return False
        if result.returncode == 1:
            return True
        raise GitError(
            "No se pudo comprobar el area de preparación.",
            returncode=result.returncode,
            stderr=result.stderr.strip(),
        )

    def uncommitted_changes(self) -> bool:
        result = self._runner.git("status", "--porcelain")
        return bool(result.stdout.strip())

    # ----------------------------------------------------------------- commits

    def commit(self, message: str, *, date: datetime | None = None) -> str:
        if not message.strip():
            raise ValidationError("El mensaje del commit no puede estar vacio.")
        env = None
        if date is not None:
            stamp = date.isoformat()
            env = {
                **os.environ,
                "GIT_AUTHOR_DATE": stamp,
                "GIT_COMMITTER_DATE": stamp,
            }
        self._runner.git("commit", "-m", message, env=env)
        sha = self._runner.git("rev-parse", "HEAD")
        return sha.stdout.strip()

    # -------------------------------------------------------------------- push

    def push(
        self,
        branch: str,
        credentials: PushCredentials,
        remote: str = REMOTE_NAME,
        *,
        timeout: float = ASKPASS_TIMEOUT,
    ) -> None:
        if not branch.strip():
            raise ValidationError("No se puede hacer push sin una rama.")
        if not credentials.token.strip():
            raise ValidationError("No se puede hacer push sin un token.")

        with self._askpass_environment(credentials) as environment:
            self._runner.run(
                (
                    "git",
                    "-c",
                    "credential.helper=",
                    "push",
                    remote,
                    f"HEAD:refs/heads/{branch}",
                ),
                env=environment,
                timeout=timeout,
            )

    @contextmanager
    def _askpass_environment(self, credentials: PushCredentials) -> Iterator[dict[str, str]]:
        """Crea un askpass temporal; el token vive solo en un archivo efímero."""
        directory = Path(tempfile.mkdtemp(prefix="autocommit-askpass-"))
        try:
            os.chmod(directory, 0o700)
            token_file = directory / "token"
            token_file.write_text(credentials.token, encoding="utf-8")
            os.chmod(token_file, 0o600)

            script = directory / "askpass.sh"
            script.write_text(_ASKPASS_SCRIPT, encoding="utf-8")
            os.chmod(script, 0o700)

            environment = dict(os.environ)
            environment.update(
                {
                    "GIT_ASKPASS": str(script),
                    "GIT_ASKPASS_REQUIRE": "force",
                    "GIT_TERMINAL_PROMPT": "0",
                    "AUTOCOMMIT_ASKPASS_TOKEN_FILE": str(token_file),
                    "AUTOCOMMIT_ASKPASS_USERNAME": credentials.username or "x-access-token",
                }
            )
            yield environment
        finally:
            shutil.rmtree(directory, ignore_errors=True)

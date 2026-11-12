"""Almacenamiento seguro de tokens de acceso.

Prioridad de lectura:

1. Variable de entorno ``AUTOCOMMIT_TOKEN_<USUARIO>`` (util en CI).
2. Llavero del sistema, si ``keyring`` está instalado y disponible.
3. Archivo JSON en ``~/.config/autocommit/tokens.json`` con permisos ``0600``.

El token nunca se escribe en ``.git/config`` ni se pasa por argumentos de
proceso; el push lo entrega mediante un script ``GIT_ASKPASS`` temporal.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .errors import ValidationError
from .ui import Prompter

TOKENS_VERSION = 1


class TokenStore(Protocol):
    writable: bool

    def get(self, username: str) -> str | None:
        """Devuelve el token guardado o ``None``."""

    def set(self, username: str, token: str) -> None:
        """Guarda el token."""


def default_config_dir() -> Path:
    """Directorio de configuración de la herramienta (XDG o ``~/.config``)."""
    override = os.environ.get("AUTOCOMMIT_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "autocommit"


def read_secret_file(path: Path) -> str | None:
    """Lee un secreto de un archivo de texto, o ``None`` si no existe."""
    try:
        value = Path(path).expanduser().read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValidationError(f"No se pudo leer el archivo de secretos {path}: {exc}") from exc
    return value or None


def write_secret_file(path: Path, value: str) -> None:
    """Escribe un secreto con permisos ``0600`` y reemplazo atómico."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        # Sistemas de archivos sin permisos POSIX.
        os.chmod(path.parent, 0o700)

    descriptor, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}-", suffix=".tmp"
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value.rstrip("\n") + "\n")
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


class FileTokenStore:
    """Tokens en JSON con permisos restrictivos y escritura atomica."""

    writable = True

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_config_dir() / "tokens.json"

    def get(self, username: str) -> str | None:
        return self._load().get(username)

    def set(self, username: str, token: str) -> None:
        tokens = self._load()
        tokens[username] = token
        self._save(tokens)

    def delete(self, username: str) -> None:
        tokens = self._load()
        if tokens.pop(username, None) is not None:
            self._save(tokens)

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(
                f"No se pudo leer {self.path}. Corrigelo o eliminalo: {exc}"
            ) from exc
        if payload.get("version") != TOKENS_VERSION:
            raise ValidationError(
                f"El archivo de tokens {self.path} tiene una versión incompatible."
            )
        tokens = payload.get("tokens", {})
        if not isinstance(tokens, dict):
            raise ValidationError(f"El archivo de tokens {self.path} está mal formado.")
        return {str(key): str(value) for key, value in tokens.items()}

    def _save(self, tokens: dict[str, str]) -> None:
        payload = json.dumps(
            {"version": TOKENS_VERSION, "tokens": tokens},
            indent=2,
            ensure_ascii=False,
        )
        write_secret_file(self.path, payload)


class EnvironmentTokenStore:
    """Lee tokens de variables de entorno; no permite escritura."""

    writable = False

    def __init__(self, prefix: str = "AUTOCOMMIT_TOKEN_") -> None:
        self._prefix = prefix

    def variable_name(self, username: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9]", "_", username).upper()
        return f"{self._prefix}{normalized}"

    def get(self, username: str) -> str | None:
        return os.environ.get(self.variable_name(username)) or None

    def set(self, username: str, token: str) -> None:
        raise ValidationError(
            "Los tokens de entorno son de solo lectura; define la variable "
            f"{self.variable_name(username)} manualmente."
        )


class KeyringTokenStore:
    """Integración opcional con el llavero del sistema."""

    writable = True

    def __init__(self, service: str = "autocommit") -> None:
        self._service = service
        try:
            import keyring  # type: ignore[import-not-found]
        except Exception:  # pragma: no cover - depende del entorno
            keyring = None
        self._keyring = keyring

    @property
    def available(self) -> bool:
        return self._keyring is not None

    def get(self, username: str) -> str | None:
        if self._keyring is None:
            return None
        try:
            return self._keyring.get_password(self._service, username)
        except Exception:  # pragma: no cover - backend del llavero no disponible
            return None

    def set(self, username: str, token: str) -> None:
        if self._keyring is None:
            raise ValidationError("El llavero del sistema no está disponible.")
        self._keyring.set_password(self._service, username, token)


@dataclass
class ChainedTokenStore:
    """Consulta varios almacenes en orden y escribe en el primero escribible."""

    stores: Sequence[TokenStore] = field(default_factory=tuple)

    def get(self, username: str) -> str | None:
        for store in self.stores:
            token = store.get(username)
            if token:
                return token
        return None

    def set(self, username: str, token: str) -> None:
        for store in self.stores:
            if store.writable:
                store.set(username, token)
                return
        raise ValidationError("No hay un almacen de tokens escribible disponible.")


def build_default_token_store() -> ChainedTokenStore:
    return ChainedTokenStore(
        stores=(EnvironmentTokenStore(), KeyringTokenStore(), FileTokenStore())
    )


class TokenProvider:
    """Obtiene tokens del almacen o los solicita de forma segura."""

    def __init__(
        self,
        store: TokenStore,
        prompter: Prompter,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._store = store
        self._prompter = prompter
        self._logger = logger or logging.getLogger("autocommit.credentials")
        self._cache: dict[str, str] = {}

    def resolve(self, username: str) -> str:
        cached = self._cache.get(username)
        if cached:
            return cached

        token = self._store.get(username)
        if token:
            self._logger.debug("Token cargado desde el almacen para @%s.", username)
        else:
            token = self._prompter.secret(f"Token de GitHub para @{username}: ")
            token = (token or "").strip()
            if not token:
                raise ValidationError(
                    f"El token de @{username} no puede estar vacio. "
                    "Genera un PAT en https://github.com/settings/tokens"
                )
            if any(character.isspace() for character in token):
                raise ValidationError(
                    f"El token de @{username} contiene espacios; revisa el valor."
                )
            self._store.set(username, token)
            self._logger.info("Token de @%s guardado de forma segura.", username)

        self._cache[username] = token
        return token

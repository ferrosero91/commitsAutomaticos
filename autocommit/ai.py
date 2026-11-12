"""Mensajes de commit generados con IA a través de OpenCode (V2).

El cliente habla el protocolo OpenAI Chat Completions, que OpenCode Console y
OpenCode Go exponen en:

    Pay-as-you-go: https://opencode.ai/inference/openai/v1
    Go:            https://opencode.ai/inference/go/openai/v1

La integración es opcional y nunca bloquea una ejecución: si la red, la clave
o la respuesta fallan, se usa el generador heurístico. Solo se envían las rutas
de los archivos, no su contenido.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import __version__
from .credentials import default_config_dir, read_secret_file, write_secret_file
from .errors import AIError, ValidationError
from .messages import (
    MAX_SUBJECT_LENGTH,
    CommitMessageFactory,
    ConventionalCommitMessageFactory,
)
from .ui import Prompter

DEFAULT_BASE_URL = "https://opencode.ai/inference/go/openai/v1"
PAY_AS_YOU_GO_BASE_URL = "https://opencode.ai/inference/openai/v1"
LEGACY_ZEN_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash"
DEFAULT_API_KEY_ENV = "OPENCODE_API_KEY"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_CALLS = 200
MAX_RETRIES = 2
RETRY_DELAY = 1.0
MAX_PROMPT_FILES = 80

CONVENTIONAL_RE = re.compile(
    r"^(?P<type>feat|fix|docs|test|style|chore|refactor|perf|build|ci)"
    r"(?P<scope>\([a-z0-9][a-z0-9._-]*\))?!?: (?P<subject>\S.*)$"
)

SYSTEM_PROMPT = """\
Eres un asistente que escribe mensajes de commit siguiendo Conventional Commits.
Reglas:
- Devuelve exactamente una línea, sin comillas, sin bloques de código y sin punto final.
- Formato: tipo(scope opcional): descripción.
- Tipos permitidos: feat, fix, docs, test, style, chore, refactor, perf, build, ci.
- La descripción va en español, en imperativo y con máximo 60 caracteres.
- Describe el grupo de archivos; no inventes cambios que las rutas no sugieran.
"""


@dataclass(frozen=True)
class AIConfig:
    """Configuración del backend de mensajes con IA."""

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    max_calls: int = DEFAULT_MAX_CALLS
    session_id: str = field(default_factory=lambda: f"ses_{uuid.uuid4().hex}")

    def __post_init__(self) -> None:
        api_key = (self.api_key or "").strip()
        base_url = (self.base_url or "").strip().rstrip("/")
        model = (self.model or "").strip()

        if not api_key:
            raise ValidationError(
                "Falta la clave de OpenCode. Defínela en la variable de entorno "
                f"{DEFAULT_API_KEY_ENV}."
            )
        if not base_url:
            raise ValidationError("La URL base de la IA no puede estar vacía.")
        if not base_url.startswith(("http://", "https://")):
            raise ValidationError("La URL base de la IA debe usar http o https.")
        if not model:
            raise ValidationError("El modelo de IA no puede estar vacío.")
        if self.timeout <= 0:
            raise ValidationError("El timeout de la IA debe ser mayor que 0.")
        if self.max_calls < 0:
            raise ValidationError("El máximo de llamadas a la IA no puede ser negativo.")

        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "model", model)

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"


def api_key_from_env(variable: str = DEFAULT_API_KEY_ENV) -> str | None:
    value = os.environ.get(variable, "").strip()
    return value or None


def default_key_file() -> Path:
    """Archivo de clave recomendado: ``~/.config/autocommit/opencode_key``."""
    return default_config_dir() / "opencode_key"


def resolve_api_key(
    *,
    explicit: str | None = None,
    env_var: str = DEFAULT_API_KEY_ENV,
    key_file: Path | None = None,
    prompter: Prompter | None = None,
    logger: logging.Logger | None = None,
) -> str:
    """Resuelve la clave: argumento, entorno, archivo 0600 o prompt seguro.

    El orden es deliberado: lo explícito gana, el entorno permite CI y el
    archivo evita repetir la clave sin dejarla en el historial del shell.
    """
    log = logger or logging.getLogger("autocommit.ai")
    path = Path(key_file).expanduser() if key_file is not None else default_key_file()

    if explicit and explicit.strip():
        log.warning(
            "La clave pasada con --ai-api-key puede quedar en el historial del shell "
            "y en la lista de procesos; prefiere la variable de entorno o el archivo."
        )
        return explicit.strip()

    env_value = api_key_from_env(env_var)
    if env_value:
        return env_value

    stored = read_secret_file(path)
    if stored:
        log.debug("Clave de OpenCode cargada desde %s.", path)
        return stored

    if prompter is None:
        raise ValidationError(
            "No hay clave de OpenCode. Define la variable "
            f"{env_var}, usa --ai-api-key-file {path} "
            "o ejecuta de forma interactiva para introducirla una vez."
        )

    value = (prompter.secret("Clave de OpenCode: ") or "").strip()
    if not value:
        raise ValidationError("La clave de OpenCode no puede estar vacía.")
    if prompter.confirm(f"¿Guardar la clave en {path}?", default=True):
        write_secret_file(path, value)
        log.info("Clave de OpenCode guardada en %s con permisos 0600.", path)
    return value


class ChatClient(Protocol):
    def complete(self, system: str, user: str) -> str:
        """Devuelve el texto generado por el modelo."""


def build_user_prompt(files: Sequence[str], limit: int = MAX_PROMPT_FILES) -> str:
    lines = [f"- {path}" for path in files[:limit]]
    remaining = len(files) - limit
    if remaining > 0:
        lines.append(f"- ... y {remaining} archivo(s) más")
    return "Archivos incluidos en el commit:\n" + "\n".join(lines)


def _clean_line(value: str) -> str:
    candidate = value.strip().strip("`'\" ").strip()
    candidate = re.sub(r"^[-*]\s+", "", candidate)
    candidate = re.sub(r"^(?:mensaje|commit)\s*:\s*", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\s+", " ", candidate).strip()
    return candidate.rstrip(".").strip()


def _truncate(candidate: str) -> str:
    if len(candidate) <= MAX_SUBJECT_LENGTH:
        return candidate
    if ": " in candidate:
        prefix, subject = candidate.split(": ", 1)
        available = MAX_SUBJECT_LENGTH - len(prefix) - 2 - 3
        if available > 0:
            return f"{prefix}: {subject[:available].rstrip(' ,.;')}..."
    return f"{candidate[: MAX_SUBJECT_LENGTH - 3].rstrip(' ,.;')}..."


def sanitize_message(text: str) -> str | None:
    """Extrae el primer mensaje válido de una respuesta del modelo."""
    if not text:
        return None
    for raw_line in text.strip().splitlines():
        candidate = _clean_line(raw_line)
        if not candidate:
            continue
        candidate = _truncate(candidate)
        if CONVENTIONAL_RE.match(candidate):
            return candidate
    return None


class OpenCodeChatClient:
    """Cliente HTTP para el endpoint OpenAI-compatible de OpenCode."""

    def __init__(
        self,
        config: AIConfig,
        *,
        logger: logging.Logger | None = None,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._logger = logger or logging.getLogger("autocommit.ai")
        self._opener = opener or urllib.request.urlopen
        self._sleep = sleep

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 200,
        }
        request = urllib.request.Request(
            self._config.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"autocommit/{__version__}",
                "x-opencode-session": self._config.session_id,
            },
        )

        last_error: AIError | None = None
        for attempt in range(MAX_RETRIES):
            try:
                with self._opener(request, timeout=self._config.timeout) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                return self._extract_content(raw)
            except urllib.error.HTTPError as exc:
                detail = _read_error_body(exc)
                last_error = AIError(
                    f"OpenCode respondió HTTP {exc.code}" + (f": {detail}" if detail else "")
                )
                if exc.code not in (429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = AIError(f"No se pudo conectar con OpenCode: {exc}")
            except AIError as exc:
                last_error = exc
                break

            if attempt + 1 < MAX_RETRIES:
                self._logger.debug(
                    "Reintentando la llamada a la IA (%d/%d).", attempt + 1, MAX_RETRIES
                )
                self._sleep(RETRY_DELAY)

        raise last_error or AIError("La IA no devolvió una respuesta.")

    @staticmethod
    def _extract_content(raw: str) -> str:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AIError("La respuesta de la IA no es JSON válido.") from exc

        choices = payload.get("choices") or []
        if not choices:
            raise AIError("La respuesta de la IA no contiene 'choices'.")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        if not isinstance(content, str) or not content.strip():
            raise AIError("La respuesta de la IA está vacía.")
        return content


def _read_error_body(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace").strip()
    except Exception:  # pragma: no cover - defensivo
        return ""
    if not body:
        return ""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:200]
    detail = payload.get("error") or payload.get("message") or payload
    return str(detail)[:200]


class AIMessageFactory:
    """Genera mensajes con IA y cae en la heurística ante cualquier fallo."""

    def __init__(
        self,
        client: ChatClient,
        fallback: CommitMessageFactory | None = None,
        *,
        max_calls: int = DEFAULT_MAX_CALLS,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._fallback = fallback or ConventionalCommitMessageFactory(random.Random())
        self._max_calls = max_calls
        self._logger = logger or logging.getLogger("autocommit.ai")
        self._calls = 0
        self._failures = 0
        self._warn_budget = 3
        self._limit_logged = False

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def failures(self) -> int:
        return self._failures

    def create(self, files: Sequence[str]) -> str:
        if not files:
            raise ValueError("Se necesita al menos un archivo para generar un mensaje.")

        if self._calls >= self._max_calls:
            if not self._limit_logged:
                self._limit_logged = True
                self._logger.info(
                    "Se alcanzó el máximo de %d llamadas a la IA; se usará la heurística.",
                    self._max_calls,
                )
            return self._fallback.create(files)

        self._calls += 1
        try:
            raw = self._client.complete(SYSTEM_PROMPT, build_user_prompt(files))
            message = sanitize_message(raw)
            if message is None:
                raise AIError("La respuesta no sigue el formato Conventional Commits.")
            self._logger.debug("Mensaje generado con IA: %s", message)
            return message
        except Exception as exc:  # la IA nunca debe romper la ejecución
            self._failures += 1
            if self._warn_budget > 0:
                self._warn_budget -= 1
                self._logger.warning(
                    "No se pudo generar el mensaje con IA (%s); se usa la heurística.",
                    exc,
                )
            else:
                self._logger.debug(
                    "No se pudo generar el mensaje con IA (%s); se usa la heurística.",
                    exc,
                )
            return self._fallback.create(files)

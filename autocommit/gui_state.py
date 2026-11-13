"""Estado, validación y preparación de la ejecución para la interfaz gráfica.

Este módulo no importa Tk: la lógica se puede probar sin abrir una ventana.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .ai import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    AIConfig,
    AIMessageFactory,
    OpenCodeChatClient,
    api_key_from_env,
    default_key_file,
)
from .app import RunRequest
from .credentials import (
    ChainedTokenStore,
    FileTokenStore,
    TokenProvider,
    build_default_token_store,
    read_secret_file,
    write_secret_file,
)
from .errors import ValidationError
from .messages import CommitMessageFactory, ConventionalCommitMessageFactory
from .models import Collaborator
from .settings import Settings
from .ui import NonInteractivePrompter


@dataclass
class CollaboratorDraft:
    """Datos editables de un colaborador en el formulario."""

    name: str = ""
    username: str = ""
    email: str = ""
    commits: int = 1
    token: str = ""
    remember: bool = True

    @property
    def label(self) -> str:
        if self.username:
            return f"@{self.username}"
        return self.name or "colaborador"

    def to_collaborator(self) -> Collaborator:
        return Collaborator(
            name=self.name,
            username=self.username,
            email=self.email,
            commits=self.commits,
        )


@dataclass
class FormState:
    """Valores actuales del formulario."""

    repo_path: Path | None = None
    remote_url: str = ""
    branch: str = "main"
    collaborators: list[CollaboratorDraft] = field(default_factory=list)
    max_files_per_commit: int = 100
    push_every: int = 1
    delay_seconds: float = 0.0
    spread_days: float = 0.0
    interleave: bool = False
    max_file_size_mb: float = 10.0
    resume: bool = False
    keep_going: bool = False
    dry_run: bool = False
    ai_enabled: bool = False
    ai_model: str = DEFAULT_MODEL
    ai_base_url: str = DEFAULT_BASE_URL
    ai_api_key: str = ""
    ai_remember_key: bool = True
    ai_context: bool = False

    def to_settings(self, *, dry_run: bool | None = None) -> Settings:
        return Settings(
            max_files_per_commit=self.max_files_per_commit,
            push_every=self.push_every,
            delay_seconds=self.delay_seconds,
            spread_days=self.spread_days,
            interleave=self.interleave,
            max_file_size_mb=self.max_file_size_mb,
            resume=self.resume,
            keep_going=self.keep_going,
            dry_run=self.dry_run if dry_run is None else dry_run,
        )

    def to_request(self) -> RunRequest:
        return RunRequest(
            repo_path=self.repo_path,
            remote_url=self.remote_url.strip() or None,
            branch=self.branch.strip() or None,
            collaborators=tuple(draft.to_collaborator() for draft in self.collaborators),
            assume_yes=True,
            non_interactive=True,
        )


class MemoryTokenStore:
    """Almacén en memoria para los tokens escritos en el formulario."""

    writable = True

    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}

    def get(self, username: str) -> str | None:
        return self.tokens.get(username)

    def set(self, username: str, token: str) -> None:
        self.tokens[username] = token


def token_available(username: str) -> bool:
    """Comprueba si existe un token en entorno, llavero o archivo seguro."""
    return bool(build_default_token_store().get(username))


def validate_form(
    state: FormState,
    *,
    dry_run: bool,
    token_lookup: Callable[[str], bool] = token_available,
) -> list[str]:
    """Devuelve una lista de errores legibles; vacía si todo está correcto."""
    errors: list[str] = []

    if state.repo_path is None:
        errors.append("Selecciona la carpeta del proyecto.")
    else:
        repo = Path(state.repo_path).expanduser()
        if not repo.exists():
            errors.append(f"La carpeta {repo} no existe.")
        elif not repo.is_dir():
            errors.append(f"{repo} no es una carpeta.")

    if not state.branch.strip():
        errors.append("Indica la rama a usar o crear.")

    if not dry_run and not state.remote_url.strip():
        errors.append("Indica la URL del remoto (o usa «Previsualizar»).")

    if not state.collaborators and not state.resume:
        errors.append("Añade al menos un colaborador o activa «Reanudar».")

    seen: set[str] = set()
    for draft in state.collaborators:
        try:
            collaborator = draft.to_collaborator()
        except ValidationError as exc:
            errors.append(f"{draft.label}: {exc}")
            continue
        if collaborator.commits < 1:
            errors.append(f"@{collaborator.username}: indica al menos 1 commit.")
        if collaborator.username in seen:
            errors.append(f"@{collaborator.username} está duplicado.")
        seen.add(collaborator.username)
        if not dry_run and not draft.token.strip() and not token_lookup(collaborator.username):
            errors.append(f"Falta el token de @{collaborator.username}: escríbelo o guárdalo.")

    try:
        state.to_settings(dry_run=dry_run)
    except ValidationError as exc:
        errors.append(str(exc))

    if state.ai_enabled:
        has_key = bool(state.ai_api_key.strip() or api_key_from_env())
        if not has_key:
            try:
                has_key = read_secret_file(default_key_file()) is not None
            except ValidationError as exc:
                errors.append(f"Clave de IA: {exc}")
        if not has_key:
            errors.append(
                "La IA está activa pero no hay clave: escríbela, define "
                "OPENCODE_API_KEY o guárdala en el archivo seguro."
            )

    return errors


def prepare_token_provider(
    state: FormState,
    logger: logging.Logger | None = None,
    *,
    persist: bool = True,
) -> TokenProvider:
    """Combina tokens del formulario con los guardados, sin pedirlos por consola."""
    memory = MemoryTokenStore()
    file_store = FileTokenStore()

    for draft in state.collaborators:
        token = draft.token.strip()
        if not token:
            continue
        memory.set(draft.username, token)
        if persist and draft.remember:
            file_store.set(draft.username, token)

    chained = ChainedTokenStore((memory, file_store))
    return TokenProvider(
        chained,
        NonInteractivePrompter(),
        logger=logger or logging.getLogger("autocommit.credentials"),
    )


def prepare_message_factory(
    state: FormState,
    settings: Settings,
    logger: logging.Logger | None = None,
    *,
    persist: bool = True,
) -> CommitMessageFactory | None:
    """Construye el generador con IA si está activado en el formulario."""
    if not state.ai_enabled:
        return None

    log = logger or logging.getLogger("autocommit.ai")
    key = state.ai_api_key.strip()
    if key and persist and state.ai_remember_key:
        write_secret_file(default_key_file(), key)
        log.info("Clave de OpenCode guardada con permisos 0600.")
    if not key:
        key = api_key_from_env() or read_secret_file(default_key_file()) or ""
    if not key:
        raise ValidationError(
            "Falta la clave de OpenCode. Escríbela en la pestaña Opciones, "
            "define OPENCODE_API_KEY o usa el archivo seguro."
        )

    config = AIConfig(
        api_key=key,
        base_url=state.ai_base_url.strip() or DEFAULT_BASE_URL,
        model=state.ai_model.strip() or DEFAULT_MODEL,
    )
    log.info("Mensajes con IA: %s en %s", config.model, config.base_url)

    rng = random.Random(settings.seed) if settings.seed is not None else None
    fallback = ConventionalCommitMessageFactory(rng)
    client = OpenCodeChatClient(config, logger=log)
    return AIMessageFactory(client, fallback, max_calls=config.max_calls, logger=log)

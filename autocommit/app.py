"""Orquestación del flujo completo.

La aplicación coordina colaboradores, no implementa detalles: delega en el
repositorio, el descubrimiento de archivos, el validador, el planificador y el
almacen de estado. Cada dependencia se inyecta para poder probarla aislada.
"""

from __future__ import annotations

import logging
import random
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .credentials import TokenProvider
from .errors import GitError, OperationCancelled, ValidationError
from .files import FileDiscovery, GitIgnoreFilter
from .git_repository import (
    REMOTE_NAME,
    GitRepository,
    Identity,
    PushCredentials,
    normalize_remote_url,
    strip_credentials,
)
from .messages import CommitMessageFactory, ConventionalCommitMessageFactory
from .models import Collaborator, CommitPlan
from .picker import select_project_folder
from .planner import CommitPlanner
from .runner import CommandRunner
from .security import FileValidator, redact
from .settings import Settings
from .state import JobState, StateStore
from .ui import Prompter, countdown

RepoFactory = Callable[[Path, CommandRunner], GitRepository]
DiscoveryFactory = Callable[[Path, "GitIgnoreFilter | None"], FileDiscovery]
ValidatorFactory = Callable[[Path], FileValidator]
StateFactory = Callable[[Path], StateStore]
FolderPicker = Callable[[Prompter], Path | None]


@dataclass(frozen=True)
class RunRequest:
    """Datos de entrada de una ejecución (CLI o interactiva)."""

    repo_path: Path | None = None
    remote_url: str | None = None
    branch: str | None = None
    collaborators: tuple[Collaborator, ...] = ()
    assume_yes: bool = False
    non_interactive: bool = False


@dataclass
class RunSummary:
    planned_commits: int = 0
    planned_files: int = 0
    commits_created: int = 0
    commits_skipped: int = 0
    files_committed: int = 0
    pushes: int = 0
    failures: int = 0
    dry_run: bool = False
    aborted: bool = False

    @property
    def ok(self) -> bool:
        return not self.aborted and self.failures == 0


class _GuardedMessageFactory:
    """Comprueba la cancelación antes de generar cada mensaje (incluida la IA)."""

    def __init__(self, factory: CommitMessageFactory, cancelled: Callable[[], bool]) -> None:
        self._factory = factory
        self._cancelled = cancelled

    def create(self, files: Sequence[str]) -> str:
        if self._cancelled():
            raise OperationCancelled("Ejecución cancelada durante la planificación.")
        return self._factory.create(files)


class Application:
    """Coordina una ejecución de principio a fin."""

    def __init__(
        self,
        settings: Settings,
        prompter: Prompter,
        tokens: TokenProvider,
        *,
        logger: logging.Logger | None = None,
        message_factory: CommitMessageFactory | None = None,
        repo_factory: RepoFactory | None = None,
        discovery_factory: DiscoveryFactory | None = None,
        validator_factory: ValidatorFactory | None = None,
        state_factory: StateFactory | None = None,
        folder_picker: FolderPicker | None = None,
        sleeper: Callable[[float], None] = countdown,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.logger = logger or logging.getLogger("autocommit.app")
        self._prompter = prompter
        self._tokens = tokens
        self._wait = sleeper
        self._cancelled = cancelled or (lambda: False)

        rng = random.Random(settings.seed) if settings.seed is not None else None
        base_messages = message_factory or ConventionalCommitMessageFactory(rng)
        self._messages = _GuardedMessageFactory(base_messages, self._cancelled)
        self._planner = CommitPlanner(settings, self._messages)

        self._repo_factory = repo_factory or self._default_repo_factory
        self._discovery_factory = discovery_factory or (
            lambda root, ignore: FileDiscovery(root, ignore)
        )
        self._validator_factory = validator_factory or self._default_validator_factory
        self._state_factory = state_factory or StateStore.in_git_dir
        self._folder_picker = folder_picker or (
            lambda prompter: select_project_folder(prompter, logger=self.logger)
        )

    # ------------------------------------------------------------------ public

    def run(self, request: RunRequest) -> RunSummary:
        summary = RunSummary(dry_run=self.settings.dry_run)
        if self._cancelled():
            summary.aborted = True
            self.logger.warning("Ejecución cancelada antes de empezar.")
            return summary

        repo = self._open_repository(request)
        resumed = self._load_state(repo)

        self._configure_remote(repo, request)
        branch = self._resolve_branch(repo, request, resumed)

        if resumed is not None:
            plan = resumed.plan
            completed = set(resumed.completed)
            pushed = resumed.pushed
        else:
            files = self._collect_files(repo)
            collaborators = self._resolve_collaborators(request)
            if self._cancelled():
                summary.aborted = True
                self.logger.warning("Ejecución cancelada antes de planificar.")
                return summary
            try:
                plan = self._planner.build(files, collaborators, branch)
            except OperationCancelled:
                summary.aborted = True
                self.logger.warning(
                    "Ejecución cancelada durante la planificación; no se modificó el repositorio."
                )
                return summary
            completed = set()
            pushed = -1

        if not self.settings.dry_run:
            repo.ensure_branch(plan.branch)

        if resumed is None and not repo.uncommitted_changes():
            self.logger.warning(
                "El árbol de trabajo está limpio: no hay archivos modificados ni sin "
                "seguimiento, así que los commits se omitirán. Usa una copia sin .git, "
                "una rama nueva o modifica archivos antes de ejecutar."
            )

        summary.planned_commits = plan.total_commits
        summary.planned_files = plan.total_files
        self._log_plan(plan, completed)

        if self._cancelled():
            summary.aborted = True
            self.logger.warning("Ejecución cancelada antes de confirmar.")
            return summary

        if not self._confirm(request, plan, completed):
            summary.aborted = True
            self.logger.warning("Operación cancelada por el usuario.")
            return summary

        pushed = self._execute(repo, plan, completed, summary, pushed)

        all_done = len(completed) == plan.total_commits and pushed >= plan.total_commits - 1
        if not self.settings.dry_run and not summary.failures and all_done:
            self._state_factory(repo.git_dir).clear()

        self._log_summary(summary, plan)
        return summary

    # ------------------------------------------------------------------- setup

    def _open_repository(self, request: RunRequest) -> GitRepository:
        path = self._resolve_repo_path(request)
        runner = CommandRunner(path, redactor=redact)
        repo = self._repo_factory(path, runner)

        if not repo.is_initialized():
            if self.settings.dry_run:
                self.logger.warning(
                    "El repositorio no está inicializado; la simulación no aplicará .gitignore."
                )
            else:
                self.logger.info("Inicializando repositorio Git en %s", path)
                repo.init()
        else:
            self.logger.info("Proyecto: %s", path)

        return repo

    def _resolve_repo_path(self, request: RunRequest) -> Path:
        path = request.repo_path
        if path is None:
            if request.non_interactive:
                raise ValidationError("Falta --repo en modo no interactivo.")
            path = self._folder_picker(self._prompter)
        if path is None:
            raise ValidationError("No se selecciono ninguna carpeta.")

        resolved = Path(path).expanduser()
        if not resolved.exists():
            raise ValidationError(f"La carpeta {resolved} no existe.")
        if not resolved.is_dir():
            raise ValidationError(f"{resolved} no es una carpeta.")
        return resolved

    def _configure_remote(self, repo: GitRepository, request: RunRequest) -> None:
        existing = repo.remote_url(REMOTE_NAME)

        if request.remote_url:
            target = normalize_remote_url(request.remote_url)
        elif existing:
            target = strip_credentials(existing)
            if target != existing:
                self.logger.warning(
                    "La URL de origin contenía credenciales; se eliminarán del repositorio."
                )
                if not self.settings.dry_run:
                    repo.set_remote_url(REMOTE_NAME, target)
        elif self.settings.dry_run:
            self.logger.info("[dry-run] Sin remoto configurado.")
            return
        elif request.non_interactive:
            raise ValidationError("Falta --remote-url en modo no interactivo.")
        else:
            target = normalize_remote_url(
                self._prompter.text(
                    "URL del repositorio remoto (https://github.com/usuario/repo.git)"
                )
            )

        if self.settings.dry_run:
            self.logger.info("[dry-run] Remoto: %s", target)
            return

        if existing is None:
            repo.add_remote(REMOTE_NAME, target)
        elif target != existing:
            repo.set_remote_url(REMOTE_NAME, target)
            self.logger.info("Remoto %s actualizado a %s", REMOTE_NAME, target)

        self.logger.info("Remoto: %s", target)
        repo.fetch(REMOTE_NAME)

    def _load_state(self, repo: GitRepository) -> JobState | None:
        if not self.settings.resume:
            return None
        if not repo.is_initialized():
            raise ValidationError("No se puede reanudar: el repositorio no está inicializado.")

        state = self._state_factory(repo.git_dir).load()
        if state is None:
            self.logger.warning("No hay estado previo que reanudar; se creará un plan nuevo.")
            return None

        self.logger.info(
            "Reanudando: %d/%d commits completados.",
            len(state.completed),
            state.plan.total_commits,
        )
        return state

    def _resolve_branch(
        self, repo: GitRepository, request: RunRequest, resumed: JobState | None
    ) -> str:
        if resumed is not None:
            if request.branch and request.branch.strip() != resumed.plan.branch:
                self.logger.warning(
                    "Se reanuda la rama %s del estado (se ignora --branch %s).",
                    resumed.plan.branch,
                    request.branch,
                )
            return resumed.plan.branch

        if request.branch:
            return request.branch.strip()
        if request.non_interactive:
            raise ValidationError("Falta --branch en modo no interactivo.")

        branches = sorted(set(repo.branches()) | set(repo.remote_branches(REMOTE_NAME)))
        if branches:
            self.logger.info("Ramas disponibles: %s", ", ".join(branches))
            selected = self._prompter.select(
                "Elige el número de la rama o deja vacio para crear una nueva",
                branches,
                allow_empty=True,
            )
            if selected is not None:
                return branches[selected]

        return self._prompter.text("Nombre de la nueva rama", default="main")

    # ------------------------------------------------------------------- files

    def _collect_files(self, repo: GitRepository) -> list[str]:
        ignore_filter = (
            GitIgnoreFilter(CommandRunner(repo.root, redactor=redact))
            if repo.is_initialized()
            else None
        )
        discovery = self._discovery_factory(repo.root, ignore_filter)
        report = discovery.discover()

        self.logger.info(
            "Archivos detectados: %d (ignorados por .gitignore: %d).",
            len(report.files),
            len(report.ignored),
        )
        if not report.files:
            raise ValidationError(
                "No se encontraron archivos para commitear. Revisa la carpeta y el .gitignore."
            )

        validator = self._validator_factory(repo.root)
        validation = validator.validate(report.files)
        for rejection in validation.rejected:
            self.logger.warning("Excluido %s: %s", rejection.path, rejection.reason)
        if validation.rejected:
            self.logger.warning(
                "Se excluyeron %d archivo(s) por seguridad. Usa --allow-secrets solo "
                "si revisaste el contenido.",
                len(validation.rejected),
            )
        if not validation.accepted:
            raise ValidationError(
                "Todos los archivos fueron rechazados por el validador de seguridad."
            )
        return list(validation.accepted)

    # ------------------------------------------------------------ collaborators

    def _resolve_collaborators(self, request: RunRequest) -> tuple[Collaborator, ...]:
        if request.collaborators:
            return tuple(request.collaborators)
        if request.non_interactive:
            raise ValidationError(
                "Falta al menos un --collaborator con formato NOMBRE:USUARIO:EMAIL:COMMITS."
            )
        return self._prompt_collaborators()

    def _prompt_collaborators(self) -> tuple[Collaborator, ...]:
        total = self._prompter.integer("Cuantos colaboradores participaran", default=1, minimum=1)
        collaborators = []
        for position in range(total):
            self.logger.info("Colaborador %d/%d", position + 1, total)
            name = self._prompter.text("  Nombre completo")
            username = self._prompter.text("  Usuario de GitHub")
            email = self._prompter.text("  Email de GitHub")
            commits = self._prompter.integer("  Commits que debe generar", default=1, minimum=1)
            collaborators.append(
                Collaborator(name=name, username=username, email=email, commits=commits)
            )
        return tuple(collaborators)

    # ------------------------------------------------------------------- plan

    def _log_plan(self, plan: CommitPlan, completed: set[int]) -> None:
        pending = plan.total_commits - len(completed)
        push_label = (
            "al final de cada colaborador"
            if self.settings.push_every == 0
            else f"cada {self.settings.push_every} commit(s) por colaborador"
        )
        self.logger.info(
            "Plan: %d commits (%d pendientes), %d archivos, %d colaborador(es).",
            plan.total_commits,
            pending,
            plan.total_files,
            plan.collaborator_count,
        )
        self.logger.info("  Rama: %s | push: %s", plan.branch, push_label)
        if plan.collaborator_count > 1:
            self.logger.info("  Cada push se autentica con el token del autor de su bloque.")
        for collaborator, count in Counter(commit.collaborator for commit in plan.commits).items():
            self.logger.info(
                "  - %s (@%s): %d commits", collaborator.name, collaborator.username, count
            )

        if self.settings.dry_run:
            for commit in plan.commits:
                prefix = "omitido" if commit.index in completed else "pendiente"
                self.logger.info(
                    "[dry-run] %02d %s | %s | %d archivo(s)",
                    commit.index,
                    prefix,
                    commit.message,
                    len(commit.files),
                )

    def _confirm(self, request: RunRequest, plan: CommitPlan, completed: set[int]) -> bool:
        if self.settings.dry_run or request.assume_yes:
            return True
        pending = plan.total_commits - len(completed)
        return self._prompter.confirm(
            f"Se crearán {pending} commits en {plan.branch} y se harán push. Continuar",
            default=False,
        )

    # ---------------------------------------------------------------- execution

    def _execute(
        self,
        repo: GitRepository,
        plan: CommitPlan,
        completed: set[int],
        summary: RunSummary,
        pushed: int,
    ) -> int:
        pending = plan.pending(completed)
        if self.settings.dry_run:
            return pushed
        if not pending and not completed:
            self.logger.info("No hay commits pendientes.")
            return pushed
        if not pending:
            self.logger.info("No hay commits pendientes; se comprobará si falta algún push.")

        if self._cancelled():
            summary.aborted = True
            self.logger.warning("Ejecución cancelada antes del primer commit.")
            return pushed

        state_store = self._state_factory(repo.git_dir)
        previous_identity = repo.capture_identity()
        current_identity: Identity | None = None
        since_push = 0
        last_pushed = pushed
        last_collaborator = self._last_collaborator(plan, completed)

        try:
            highest_completed = max(completed, default=-1)
            if (
                last_collaborator is not None
                and last_pushed < highest_completed
                and repo.has_commits()
            ):
                self._push(repo, plan.branch, last_collaborator, summary)
                last_pushed = highest_completed
                state_store.save(plan, completed, last_pushed)

            for position, commit in enumerate(pending):
                if self._cancelled():
                    summary.aborted = True
                    self.logger.warning(
                        "Detención solicitada; se conserva el estado para reanudar con --resume."
                    )
                    break

                if (
                    last_collaborator is not None
                    and commit.collaborator != last_collaborator
                    and since_push > 0
                ):
                    self.logger.debug(
                        "Cambio de colaborador: push del bloque de @%s.",
                        last_collaborator.username,
                    )
                    self._push(repo, plan.branch, last_collaborator, summary)
                    last_pushed = max(last_pushed, max(completed))
                    since_push = 0
                    state_store.save(plan, completed, last_pushed)
                    if self.settings.delay_seconds > 0 and position < len(pending) - 1:
                        self._delay(self.settings.delay_seconds)

                target_identity = Identity(commit.collaborator.name, commit.collaborator.email)
                if target_identity != current_identity:
                    repo.set_identity(target_identity)
                    current_identity = target_identity

                try:
                    staged = repo.stage(commit.files)
                    if not staged:
                        self.logger.warning(
                            "Los archivos de %r ya no existen; se omite el commit.",
                            commit.message,
                        )
                        summary.commits_skipped += 1
                        completed.add(commit.index)
                        state_store.save(plan, completed, last_pushed)
                        continue

                    if not repo.has_staged_changes():
                        self.logger.warning(
                            "Sin cambios en disco para %r (%d archivo(s)); se omite.",
                            commit.message,
                            len(commit.files),
                        )
                        summary.commits_skipped += 1
                        completed.add(commit.index)
                        state_store.save(plan, completed, last_pushed)
                        continue

                    sha = repo.commit(commit.message)
                except GitError as exc:
                    summary.failures += 1
                    self.logger.error(
                        "Fallo el commit de @%s: %s", commit.collaborator.username, exc
                    )
                    if not self.settings.keep_going:
                        raise
                    continue

                summary.commits_created += 1
                summary.files_committed += len(staged)
                since_push += 1
                last_collaborator = commit.collaborator
                completed.add(commit.index)
                state_store.save(plan, completed, last_pushed)

                self.logger.info(
                    "%s %s | %d archivo(s) | @%s",
                    sha[:8],
                    commit.message,
                    len(staged),
                    commit.collaborator.username,
                )

                if self.settings.push_every and since_push >= self.settings.push_every:
                    self._push(repo, plan.branch, last_collaborator, summary)
                    last_pushed = max(last_pushed, commit.index)
                    since_push = 0
                    state_store.save(plan, completed, last_pushed)
                    if self.settings.delay_seconds > 0 and position < len(pending) - 1:
                        self._delay(self.settings.delay_seconds)

            highest_completed = max(completed, default=-1)
            if self._cancelled():
                summary.aborted = True
                self.logger.warning(
                    "Detención solicitada; no se hará el push final y el estado "
                    "queda disponible para reanudar."
                )
            elif last_collaborator is not None and last_pushed < highest_completed:
                self._push(repo, plan.branch, last_collaborator, summary)
                last_pushed = highest_completed
                state_store.save(plan, completed, last_pushed)

            return last_pushed
        finally:
            repo.restore_identity(previous_identity)

    def _delay(self, seconds: float) -> None:
        """Espera interrumpible: reacciona a «Detener» en menos de 0,2 s."""
        if seconds <= 0 or self._cancelled():
            return
        if self._wait is not countdown:
            # Doble de prueba o espera personalizada (tests sin latencia real).
            self._wait(seconds)
            return
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._cancelled():
                return
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    @staticmethod
    def _last_collaborator(plan: CommitPlan, completed: set[int]) -> Collaborator | None:
        if not completed:
            return None
        highest = max(completed)
        for commit in plan.commits:
            if commit.index == highest:
                return commit.collaborator
        return None

    def _push(
        self,
        repo: GitRepository,
        branch: str,
        collaborator: Collaborator,
        summary: RunSummary,
    ) -> None:
        token = self._tokens.resolve(collaborator.username)
        try:
            repo.push(branch, PushCredentials(collaborator.username, token))
        except GitError:
            summary.failures += 1
            raise
        summary.pushes += 1
        self.logger.info("   Push a %s/%s completado.", REMOTE_NAME, branch)

    # ----------------------------------------------------------------- summary

    def _log_summary(self, summary: RunSummary, plan: CommitPlan) -> None:
        self.logger.info("")
        self.logger.info("=" * 60)
        if summary.dry_run:
            self.logger.info(
                "Simulación: %d commits planificados sobre %d archivos.",
                summary.planned_commits,
                plan.total_files,
            )
            return
        self.logger.info(
            "Commits creados: %d | omitidos: %d | archivos: %d | pushes: %d | errores: %d",
            summary.commits_created,
            summary.commits_skipped,
            summary.files_committed,
            summary.pushes,
            summary.failures,
        )
        if summary.ok and summary.commits_created:
            self.logger.info("Proceso completado correctamente.")

    # -------------------------------------------------------------- factories

    def _default_repo_factory(self, root: Path, runner: CommandRunner) -> GitRepository:
        return GitRepository(root, runner, logger=self.logger)

    def _default_validator_factory(self, root: Path) -> FileValidator:
        return FileValidator(
            root,
            self.settings.max_file_size_bytes,
            allow_secrets=self.settings.allow_secrets,
        )

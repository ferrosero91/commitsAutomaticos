"""Interfaz gráfica de escritorio basada en tkinter/ttk.

Solo se importa cuando se usa ``--gui`` o el comando ``autocommit-gui``, de
modo que la CLI siga funcionando en entornos sin tkinter. La lógica de
validación y preparación vive en :mod:`autocommit.gui_state` para poder
probarla sin abrir ventanas.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .ai import DEFAULT_BASE_URL, DEFAULT_MODEL
from .app import Application, RunSummary
from .credentials import default_config_dir
from .errors import AutocommitError, ValidationError
from .gui_state import (
    CollaboratorDraft,
    FormState,
    prepare_message_factory,
    prepare_token_provider,
    token_available,
    validate_form,
)
from .models import Collaborator
from .security import RedactingFilter, redact
from .ui import NonInteractivePrompter

AI_MODELS = (
    "deepseek-v4.1-flash",
    "glm-5.3-flash",
    "kimi-k2.7-code",
    "qwen3.8-flash",
)
BRANCHES = ("main", "master", "develop")
MUTED = "#6b7280"


class _QueueLogHandler(logging.Handler):
    """Envía los logs a la cola que consume el hilo de Tk."""

    def __init__(self, sink: queue.Queue) -> None:
        super().__init__()
        self._sink = sink
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = redact(self.format(record))
        except Exception:  # pragma: no cover - defensivo
            return
        if record.levelno >= logging.ERROR:
            text = f"✖ {text}"
        elif record.levelno >= logging.WARNING:
            text = f"⚠ {text}"
        self._sink.put(("log", text))


class CollaboratorDialog(tk.Toplevel):
    """Diálogo modal para añadir o editar un colaborador."""

    def __init__(self, parent: tk.Misc, draft: CollaboratorDraft | None = None) -> None:
        super().__init__(parent)
        self.title("Editar colaborador" if draft else "Añadir colaborador")
        self.resizable(False, False)
        self.transient(parent)
        self.result: CollaboratorDraft | None = None

        self._name_var = tk.StringVar(value=draft.name if draft else "")
        self._username_var = tk.StringVar(value=draft.username if draft else "")
        self._email_var = tk.StringVar(value=draft.email if draft else "")
        self._commits_var = tk.StringVar(value=str(draft.commits if draft else 1))
        self._token_var = tk.StringVar(value=draft.token if draft else "")
        self._remember_var = tk.BooleanVar(value=draft.remember if draft else True)

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)

        first_entry: ttk.Entry | None = None
        fields = (
            ("Nombre completo", self._name_var),
            ("Usuario de GitHub", self._username_var),
            ("Email de GitHub", self._email_var),
        )
        for row, (label, variable) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(frame, textvariable=variable, width=40)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            if first_entry is None:
                first_entry = entry

        ttk.Label(frame, text="Commits").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Spinbox(frame, from_=1, to=10000, textvariable=self._commits_var, width=8).grid(
            row=3, column=1, sticky="w", pady=4
        )

        ttk.Label(frame, text="Token").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self._token_var, show="•", width=40).grid(
            row=4, column=1, sticky="ew", pady=4
        )

        ttk.Checkbutton(
            frame,
            text="Guardar el token en el almacén seguro (0600)",
            variable=self._remember_var,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Label(
            frame,
            text=("Déjalo vacío para usar un token guardado o AUTOCOMMIT_TOKEN_<USUARIO>."),
            foreground=MUTED,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 12))

        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Cancelar", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Guardar", command=self._save).pack(side="right")

        frame.columnconfigure(1, weight=1)
        self.bind("<Return>", lambda _event: self._save())
        self.bind("<Escape>", lambda _event: self.destroy())
        if first_entry is not None:
            first_entry.focus_set()
        self.grab_set()
        parent.wait_window(self)

    def _save(self) -> None:
        try:
            collaborator = Collaborator(
                name=self._name_var.get(),
                username=self._username_var.get(),
                email=self._email_var.get(),
                commits=int(self._commits_var.get()),
            )
        except (ValueError, ValidationError) as exc:
            messagebox.showerror("Datos inválidos", str(exc), parent=self)
            return

        self.result = CollaboratorDraft(
            name=collaborator.name,
            username=collaborator.username,
            email=collaborator.email,
            commits=collaborator.commits,
            token=self._token_var.get().strip(),
            remember=self._remember_var.get(),
        )
        self.destroy()


class AutocommitWindow(tk.Tk):
    """Ventana principal de la aplicación."""

    def __init__(self) -> None:
        super().__init__()
        self.title(f"autocommit {__version__} — historial reproducible")
        self.geometry("980x800")
        self.minsize(880, 720)

        self._drafts: list[CollaboratorDraft] = []
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

        self._create_variables()
        self._build_ui()
        self._configure_logging()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._drain)

        logging.getLogger("autocommit").info(
            "GUI %s iniciada. Log: %s",
            __version__,
            getattr(self, "_log_path", "solo consola"),
        )

    # ------------------------------------------------------------------ layout

    def _create_variables(self) -> None:
        self._repo = tk.StringVar()
        self._remote = tk.StringVar()
        self._branch = tk.StringVar(value="main")

        self._max_files = tk.StringVar(value="100")
        self._push_every = tk.StringVar(value="1")
        self._delay = tk.StringVar(value="0")
        self._max_mb = tk.StringVar(value="10")
        self._resume = tk.BooleanVar(value=False)
        self._keep_going = tk.BooleanVar(value=False)
        self._dry_run = tk.BooleanVar(value=False)

        self._ai_enabled = tk.BooleanVar(value=False)
        self._ai_model = tk.StringVar(value=DEFAULT_MODEL)
        self._ai_base_url = tk.StringVar(value=DEFAULT_BASE_URL)
        self._ai_key = tk.StringVar()
        self._ai_remember = tk.BooleanVar(value=True)

        self._status = tk.StringVar(value="Listo.")

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        notebook.add(self._build_project_tab(notebook), text="Proyecto")
        notebook.add(self._build_collaborators_tab(notebook), text="Colaboradores")
        notebook.add(self._build_options_tab(notebook), text="Opciones")
        notebook.add(self._build_log_tab(notebook), text="Registro")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(10, 0))

        ttk.Label(actions, textvariable=self._status, foreground=MUTED).pack(side="left")
        self._progress = ttk.Progressbar(actions, mode="indeterminate", length=140)
        self._progress.pack(side="left", padx=(12, 0))

        self._stop_button = ttk.Button(
            actions, text="Detener", command=self._on_stop, state="disabled"
        )
        self._stop_button.pack(side="right")
        self._run_button = ttk.Button(
            actions, text="Ejecutar", command=lambda: self._start(dry_run=False)
        )
        self._run_button.pack(side="right", padx=(0, 8))
        self._preview_button = ttk.Button(
            actions, text="Previsualizar", command=lambda: self._start(dry_run=True)
        )
        self._preview_button.pack(side="right", padx=(0, 8))

    def _build_project_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=16)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="Carpeta del proyecto").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self._repo).grid(row=1, column=0, sticky="ew", pady=(4, 12))
        ttk.Button(frame, text="Elegir…", command=self._choose_folder).grid(
            row=1, column=1, padx=(8, 0), pady=(4, 12)
        )

        ttk.Label(frame, text="URL del remoto (origin)").grid(row=2, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self._remote).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(4, 12)
        )

        ttk.Label(frame, text="Rama").grid(row=4, column=0, sticky="w")
        ttk.Combobox(frame, textvariable=self._branch, values=BRANCHES, width=28).grid(
            row=5, column=0, sticky="w", pady=(4, 12)
        )

        ttk.Label(
            frame,
            text=(
                "Si la carpeta no es un repositorio, se inicializa. Si origin ya "
                "existe, se conserva salvo que escribas otra URL."
            ),
            foreground=MUTED,
            wraplength=620,
            justify="left",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

        return frame

    def _build_collaborators_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=16)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        columns = ("name", "username", "email", "commits", "token")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=10, selectmode="browse")
        headings = {
            "name": ("Nombre", 190),
            "username": ("Usuario", 140),
            "email": ("Email", 230),
            "commits": ("Commits", 80),
            "token": ("Token", 120),
        }
        for column, (text, width) in headings.items():
            tree.heading(column, text=text)
            tree.column(column, width=width, anchor="w" if column != "commits" else "center")
        tree.grid(row=0, column=0, columnspan=2, sticky="nsew")
        tree.bind("<Double-1>", self._on_tree_double_click)

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scrollbar.grid(row=0, column=2, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)
        self._tree = tree

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(buttons, text="Añadir", command=self._add_collaborator).pack(side="left")
        ttk.Button(buttons, text="Editar", command=self._edit_collaborator).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(buttons, text="Quitar", command=self._remove_collaborator).pack(
            side="left", padx=(8, 0)
        )

        ttk.Label(
            frame,
            text=(
                "Cada colaborador aporta su nombre, email (autoría) y token con "
                "permiso de escritura. Los tokens no se muestran."
            ),
            foreground=MUTED,
            wraplength=700,
            justify="left",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 0))

        return frame

    def _build_options_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=16)
        frame.columnconfigure(1, weight=1)

        rows = (
            ("Máx. archivos por commit", self._max_files, 1, 10000),
            ("Commits entre pushes", self._push_every, 0, 10000),
            ("Espera entre pushes (s)", self._delay, 0, 3600),
            ("Tamaño máx. por archivo (MB)", self._max_mb, 1, 1000),
        )
        for row, (label, variable, minimum, maximum) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Spinbox(
                frame,
                from_=minimum,
                to=maximum,
                textvariable=variable,
                width=12,
            ).grid(row=row, column=1, sticky="w", pady=4)

        ttk.Label(
            frame,
            text=(
                "Al cambiar de colaborador se fuerza un push con su token; "
                "nunca se mezclan autores en un mismo push."
            ),
            foreground=MUTED,
            wraplength=700,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        checks = ttk.Frame(frame)
        checks.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 4))
        ttk.Checkbutton(
            checks, text="Reanudar desde el estado guardado", variable=self._resume
        ).pack(anchor="w")
        ttk.Checkbutton(
            checks, text="Continuar si un commit falla", variable=self._keep_going
        ).pack(anchor="w")
        ttk.Checkbutton(
            checks, text="Simulación (no modifica el repositorio)", variable=self._dry_run
        ).pack(anchor="w")

        ai = ttk.LabelFrame(frame, text="Mensajes con IA (OpenCode)", padding=12)
        ai.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ai.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            ai,
            text="Generar mensajes con IA (con respaldo heurístico)",
            variable=self._ai_enabled,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        ttk.Label(ai, text="Modelo").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Combobox(ai, textvariable=self._ai_model, values=AI_MODELS, width=30).grid(
            row=1, column=1, sticky="w", pady=3
        )

        ttk.Label(ai, text="URL base").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(ai, textvariable=self._ai_base_url).grid(row=2, column=1, sticky="ew", pady=3)

        ttk.Label(ai, text="Clave").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(ai, textvariable=self._ai_key, show="•").grid(
            row=3, column=1, sticky="ew", pady=3
        )
        ttk.Checkbutton(
            ai,
            text="Guardar la clave con permisos 0600",
            variable=self._ai_remember,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        ttk.Label(
            ai,
            text=(
                "También se usa OPENCODE_API_KEY o el archivo ~/.config/autocommit/opencode_key."
            ),
            foreground=MUTED,
            wraplength=620,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        return frame

    def _build_log_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=12)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self._log = ScrolledText(
            frame, height=20, wrap="word", state="disabled", font=("Menlo", 11)
        )
        self._log.grid(row=0, column=0, sticky="nsew")
        return frame

    # --------------------------------------------------------------- actions

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(parent=self, title="Selecciona la carpeta del proyecto")
        if selected:
            self._repo.set(selected)

    def _refresh_tree(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)
        for index, draft in enumerate(self._drafts):
            if draft.token:
                token_state = "definido"
            elif self._token_lookup(draft.username):
                token_state = "guardado"
            else:
                token_state = "sin definir"
            self._tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    draft.name,
                    draft.username,
                    draft.email,
                    draft.commits,
                    token_state,
                ),
            )

    def _add_collaborator(self) -> None:
        dialog = CollaboratorDialog(self)
        if dialog.result is not None:
            self._drafts.append(dialog.result)
            self._refresh_tree()

    def _selected_index(self) -> int | None:
        selection = self._tree.selection()
        if not selection:
            return None
        return int(selection[0])

    def _edit_collaborator(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        dialog = CollaboratorDialog(self, self._drafts[index])
        if dialog.result is not None:
            self._drafts[index] = dialog.result
            self._refresh_tree()

    def _remove_collaborator(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        del self._drafts[index]
        self._refresh_tree()

    def _on_tree_double_click(self, event: tk.Event) -> None:
        item = self._tree.identify_row(event.y)
        if item:
            self._tree.selection_set(item)
            self._edit_collaborator()

    # ------------------------------------------------------------------- run

    def _collect_state(self) -> FormState:
        repo_text = self._repo.get().strip()

        return FormState(
            repo_path=Path(repo_text).expanduser() if repo_text else None,
            remote_url=self._remote.get().strip(),
            branch=self._branch.get().strip(),
            collaborators=list(self._drafts),
            max_files_per_commit=self._parse_int(self._max_files.get(), 0),
            push_every=self._parse_int(self._push_every.get(), -1),
            delay_seconds=self._parse_float(self._delay.get(), -1.0),
            max_file_size_mb=self._parse_float(self._max_mb.get(), 0.0),
            resume=self._resume.get(),
            keep_going=self._keep_going.get(),
            dry_run=self._dry_run.get(),
            ai_enabled=self._ai_enabled.get(),
            ai_model=self._ai_model.get().strip(),
            ai_base_url=self._ai_base_url.get().strip(),
            ai_api_key=self._ai_key.get().strip(),
            ai_remember_key=self._ai_remember.get(),
        )

    @staticmethod
    def _parse_int(raw: str, fallback: int) -> int:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _parse_float(raw: str, fallback: float) -> float:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return fallback

    def _token_lookup(self, username: str) -> bool:
        try:
            return token_available(username)
        except Exception:
            return False

    def _start(self, *, dry_run: bool) -> None:
        if self._worker is not None and self._worker.is_alive():
            return

        state = self._collect_state()
        errors = validate_form(state, dry_run=dry_run, token_lookup=self._token_lookup)
        if errors:
            messagebox.showerror(
                "Revisa el formulario",
                "\n".join(f"• {error}" for error in errors),
                parent=self,
            )
            return

        self._clear_log()
        self._stop.clear()
        self._set_running(True)
        self._worker = threading.Thread(target=self._run_worker, args=(state, dry_run), daemon=True)
        self._worker.start()

    def _run_worker(self, state: FormState, dry_run: bool) -> None:
        logger = logging.getLogger("autocommit")
        try:
            logger.info("Inicio: %s", "simulación" if dry_run else "ejecución real")
            settings = state.to_settings(dry_run=dry_run)
            tokens = prepare_token_provider(state, logger, persist=not dry_run)
            message_factory = prepare_message_factory(state, settings, logger, persist=not dry_run)
            application = Application(
                settings,
                NonInteractivePrompter(),
                tokens,
                logger=logger,
                message_factory=message_factory,
                cancelled=self._stop.is_set,
            )
            summary = application.run(state.to_request())
            self._queue.put(("done", summary))
        except AutocommitError as exc:
            self._queue.put(("error", str(exc)))
        except Exception as exc:  # la GUI no debe morir por un fallo inesperado
            self._queue.put(("error", f"Error inesperado: {exc}"))

    def _on_stop(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            return
        self._stop.set()
        self._stop_button.state(["disabled"])
        self._status.set("Deteniendo… (se terminará la operación actual)")
        logging.getLogger("autocommit").warning(
            "Detención solicitada: se cancelará la planificación/IA pendiente y "
            "se parará entre commits. Una operación de git en curso termina antes."
        )

    def _set_running(self, running: bool) -> None:
        for button in (self._preview_button, self._run_button):
            button.state(["disabled"] if running else ["!disabled"])
        self._stop_button.state(["!disabled"] if running else ["disabled"])
        if running:
            self._status.set("Ejecutando…")
            self._progress.start(12)
        else:
            self._progress.stop()
            self._status.set("Listo.")

    def _finish(self, summary: object) -> None:
        assert isinstance(summary, RunSummary)
        self._worker = None
        self._set_running(False)

        if summary.dry_run:
            self._append(
                f"Simulación completada: {summary.planned_commits} commits "
                f"sobre {summary.planned_files} archivos."
            )
            messagebox.showinfo(
                "Simulación completada",
                f"{summary.planned_commits} commits planificados sobre "
                f"{summary.planned_files} archivos.\n\n"
                "No se modificó el repositorio.",
                parent=self,
            )
            return

        if summary.aborted:
            self._append("Detenido. El estado permite continuar con «Reanudar».")
            messagebox.showwarning(
                "Detenido",
                "La ejecución se detuvo. Activa «Reanudar» y vuelve a ejecutar para continuar.",
                parent=self,
            )
            return

        detail = (
            f"Commits: {summary.commits_created}\n"
            f"Archivos: {summary.files_committed}\n"
            f"Pushes: {summary.pushes}\n"
            f"Errores: {summary.failures}"
        )
        if summary.ok:
            self._append("Proceso completado.")
            messagebox.showinfo("Listo", detail, parent=self)
        else:
            self._append("Proceso completado con errores.")
            messagebox.showwarning("Con errores", detail, parent=self)

    def _finish_error(self, message: str) -> None:
        self._worker = None
        self._set_running(False)
        self._append(f"✖ {message}")
        messagebox.showerror("Error", message, parent=self)

    # ------------------------------------------------------------------ logs

    def _configure_logging(self) -> None:
        logger = logging.getLogger("autocommit")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.addHandler(_QueueLogHandler(self._queue))

        self._log_path: Path | None = None
        try:
            log_path = default_config_dir() / "gui.log"
            log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
            file_handler.addFilter(RedactingFilter())
            logger.addHandler(file_handler)
            self._log_path = log_path
        except OSError:
            # Si no se puede escribir el log, la GUI sigue funcionando.
            self._log_path = None

    def report_callback_exception(self, exc, value, traceback) -> None:
        """Registra los errores de callbacks de Tk en vez de solo imprimirlos."""
        logging.getLogger("autocommit").error(
            "Error en la interfaz: %s", value, exc_info=(exc, value, traceback)
        )

    def _clear_log(self) -> None:
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def _append(self, text: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", text.rstrip() + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._append(str(payload))
                elif kind == "done":
                    self._finish(payload)
                elif kind == "error":
                    self._finish_error(str(payload))
        except queue.Empty:
            pass
        self.after(150, self._drain)

    # ----------------------------------------------------------------- close

    def _on_close(self) -> None:
        logging.getLogger("autocommit").info("Cerrando la ventana de la GUI.")
        if self._worker is not None and self._worker.is_alive():
            if not messagebox.askyesno(
                "Salir",
                "Hay una ejecución en curso. ¿Detenerla y cerrar?",
                parent=self,
            ):
                return
            self._stop.set()
        self.destroy()


def main() -> int:
    """Abre la interfaz gráfica."""
    try:
        window = AutocommitWindow()
    except tk.TclError as exc:
        print(f"No se pudo abrir la interfaz gráfica: {exc}", file=sys.stderr)
        return 2
    logger = logging.getLogger("autocommit")
    try:
        window.mainloop()
    finally:
        logger.info("GUI cerrada.")
    return 0


launch_gui = main

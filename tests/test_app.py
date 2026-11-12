from __future__ import annotations

import random
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from autocommit.app import Application, RunRequest
from autocommit.credentials import FileTokenStore, TokenProvider
from autocommit.errors import ValidationError
from autocommit.git_repository import GitRepository
from autocommit.messages import ConventionalCommitMessageFactory
from autocommit.models import Collaborator
from autocommit.planner import CommitPlanner
from autocommit.settings import Settings
from autocommit.state import StateStore
from tests.support import ScriptedPrompter, TemporaryProjectTestCase, null_logger

TOKEN = "ghp_" + "d" * 36


class ApplicationTests(TemporaryProjectTestCase):
    def setUp(self) -> None:
        super().setUp()
        # El almacen de tokens vive fuera del proyecto para no commitearlo.
        self._tokens_dir = tempfile.TemporaryDirectory(prefix="autocommit-tokens-")
        self.addCleanup(self._tokens_dir.cleanup)

    def make_app(
        self,
        settings: Settings,
        prompter: ScriptedPrompter | None = None,
        **kwargs: object,
    ) -> Application:
        prompter = prompter or ScriptedPrompter()
        store = FileTokenStore(Path(self._tokens_dir.name) / "tokens.json")
        store.set("ada", TOKEN)
        store.set("alan", TOKEN)
        store.set("carol", TOKEN)
        tokens = TokenProvider(store, prompter, logger=null_logger("tokens"))
        return Application(
            settings,
            prompter,
            tokens,
            logger=null_logger("app"),
            sleeper=lambda _seconds: None,
            **kwargs,  # type: ignore[arg-type]
        )

    def collaborators(self) -> tuple[Collaborator, ...]:
        return (
            Collaborator("Ada Lovelace", "ada", "ada@example.com", 2),
            Collaborator("Alan Turing", "alan", "alan@example.com", 2),
        )

    def test_dry_run_does_not_modify_repository(self) -> None:
        repo = self.init_repo()
        self.write("src/a.py", "print('a')\n")
        self.write("docs/b.md", "# b\n")
        app = self.make_app(Settings(dry_run=True))

        summary = app.run(
            RunRequest(
                repo_path=repo,
                remote_url=str(self.tmp / "remote.git"),
                branch="main",
                collaborators=self.collaborators()[:1],
                assume_yes=True,
                non_interactive=True,
            )
        )

        self.assertTrue(summary.ok)
        self.assertEqual(summary.planned_commits, 2)
        self.assertEqual(summary.planned_files, 2)
        self.assertEqual(summary.commits_created, 0)
        self.assertEqual(summary.pushes, 0)
        self.assertNotEqual(self.git("rev-parse", "--verify", "HEAD").returncode, 0)
        self.assertEqual(self.git_stdout("remote").strip(), "")
        self.assertFalse((repo / ".git" / "autocommit" / "state.json").exists())

    def test_executes_commits_and_pushes_to_remote(self) -> None:
        repo = self.init_repo()
        for name in ("a", "b", "c", "d"):
            self.write(f"src/{name}.py", f"print('{name}')\n")
        remote = self.init_bare_remote()
        app = self.make_app(Settings(push_every=0))

        summary = app.run(
            RunRequest(
                repo_path=repo,
                remote_url=str(remote),
                branch="main",
                collaborators=self.collaborators(),
                assume_yes=True,
                non_interactive=True,
            )
        )

        self.assertTrue(summary.ok, summary)
        self.assertEqual(summary.commits_created, 4)
        self.assertEqual(summary.files_committed, 4)
        self.assertEqual(summary.pushes, 2)
        self.assertEqual(summary.failures, 0)

        emails = self.git_stdout("log", "--format=%ae", "main").split()
        self.assertEqual(emails.count("ada@example.com"), 2)
        self.assertEqual(emails.count("alan@example.com"), 2)

        remote_count = self.git_stdout("rev-list", "--count", "main", cwd=remote).strip()
        self.assertEqual(remote_count, "4")

        subjects = self.git_stdout("log", "--format=%s", "main").splitlines()
        for subject in subjects:
            self.assertRegex(subject, r"^(feat|fix|docs|test|style|chore)")

        config = (repo / ".git" / "config").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, config)
        self.assertNotIn("ghp_", config)

        # La identidad local previa (inexistente) se restaura al terminar.
        self.assertEqual(self.git("config", "--local", "--get", "user.name").returncode, 1)
        self.assertFalse((repo / ".git" / "autocommit" / "state.json").exists())

    def test_excludes_files_with_secrets(self) -> None:
        repo = self.init_repo()
        self.write("src/a.py", "print('a')\n")
        self.write("docs/b.md", "# b\n")
        self.write(".env", f"TOKEN={TOKEN}\n")
        app = self.make_app(Settings(dry_run=True))
        collaborator = Collaborator("Ada Lovelace", "ada", "ada@example.com", 1)

        summary = app.run(
            RunRequest(
                repo_path=repo,
                remote_url=str(self.tmp / "remote.git"),
                branch="main",
                collaborators=(collaborator,),
                assume_yes=True,
                non_interactive=True,
            )
        )

        self.assertEqual(summary.planned_files, 2)
        self.assertEqual(summary.planned_commits, 1)

    def test_resume_skips_completed_commits(self) -> None:
        repo = self.init_repo()
        files = [f"src/file_{index}.py" for index in range(4)]
        for name in files:
            self.write(name, "print('x')\n")
        remote = self.init_bare_remote()

        collaborator = Collaborator("Ada Lovelace", "ada", "ada@example.com", 2)
        plan = CommitPlanner(Settings(), ConventionalCommitMessageFactory(random.Random(1))).build(
            files, (collaborator,), "main"
        )
        state_store = StateStore.in_git_dir(GitRepository(repo, logger=null_logger("git")).git_dir)
        state_store.save(plan, {0})

        app = self.make_app(Settings(resume=True, push_every=0))
        summary = app.run(
            RunRequest(
                repo_path=repo,
                remote_url=str(remote),
                collaborators=(),
                assume_yes=True,
                non_interactive=True,
            )
        )

        self.assertTrue(summary.ok, summary)
        self.assertEqual(summary.planned_commits, 2)
        self.assertEqual(summary.commits_created, 1)
        subjects = self.git_stdout("log", "--format=%s", "main").splitlines()
        self.assertEqual(subjects, [plan.commits[1].message])

    def test_resume_pushes_commits_left_unpushed(self) -> None:
        repo = self.init_repo()
        self.write("a.py", "print('a')\n")
        self.git("config", "user.name", "Prev")
        self.git("config", "user.email", "prev@example.com")
        self.git("add", "a.py")
        self.git("commit", "-q", "-m", "feat: add a")
        remote = self.init_bare_remote()

        collaborator = Collaborator("Ada Lovelace", "ada", "ada@example.com", 1)
        plan = CommitPlanner(Settings(), ConventionalCommitMessageFactory(random.Random(1))).build(
            ["a.py"], (collaborator,), "main"
        )
        state_store = StateStore.in_git_dir(GitRepository(repo, logger=null_logger("git")).git_dir)
        state_store.save(plan, {0}, pushed=-1)

        app = self.make_app(Settings(resume=True))
        summary = app.run(
            RunRequest(
                repo_path=repo,
                remote_url=str(remote),
                collaborators=(),
                assume_yes=True,
                non_interactive=True,
            )
        )

        self.assertTrue(summary.ok, summary)
        self.assertEqual(summary.commits_created, 0)
        self.assertEqual(summary.pushes, 1)
        remote_count = self.git_stdout("rev-list", "--count", "main", cwd=remote).strip()
        self.assertEqual(remote_count, "1")
        self.assertEqual(self.git_stdout("config", "--local", "--get", "user.name").strip(), "Prev")
        self.assertFalse(state_store.exists())

    def test_each_collaborator_pushes_with_own_token(self) -> None:
        repo = self.init_repo()
        for name in ("a", "b", "c", "d"):
            self.write(f"src/{name}.py", f"print('{name}')\n")
        remote = self.init_bare_remote()

        collaborators = (
            Collaborator("Ada Lovelace", "ada", "ada@example.com", 1),
            Collaborator("Alan Turing", "alan", "alan@example.com", 2),
            Collaborator("Grace Hopper", "carol", "carol@example.com", 1),
        )
        app = self.make_app(Settings(push_every=3))
        pushes: list[str] = []
        original_push = GitRepository.push

        def recording_push(
            repo_self: GitRepository,
            branch: str,
            credentials: object,
            remote_name: str = "origin",
            *,
            timeout: float = 300,
        ) -> None:
            pushes.append(credentials.username)  # type: ignore[attr-defined]
            original_push(
                repo_self,
                branch,
                credentials,
                remote_name,
                timeout=timeout,  # type: ignore[arg-type]
            )

        with mock.patch.object(GitRepository, "push", recording_push):
            summary = app.run(
                RunRequest(
                    repo_path=repo,
                    remote_url=str(remote),
                    branch="main",
                    collaborators=collaborators,
                    assume_yes=True,
                    non_interactive=True,
                )
            )

        self.assertTrue(summary.ok, summary)
        self.assertEqual(summary.commits_created, 4)
        self.assertEqual(pushes, ["ada", "alan", "carol"])

        emails = self.git_stdout("log", "--format=%ae", "main").split()
        self.assertEqual(emails.count("ada@example.com"), 1)
        self.assertEqual(emails.count("alan@example.com"), 2)
        self.assertEqual(emails.count("carol@example.com"), 1)

    def test_cancellation_between_commits_keeps_resumable_state(self) -> None:
        repo = self.init_repo()
        self.write("a.py", "print('a')\n")
        self.write("b.py", "print('b')\n")
        remote = self.init_bare_remote()

        event = threading.Event()
        app = self.make_app(Settings(push_every=0), cancelled=event.is_set)
        original_commit = GitRepository.commit

        def commit_and_cancel(repo_self: GitRepository, message: str) -> str:
            sha = original_commit(repo_self, message)
            event.set()
            return sha

        with mock.patch.object(GitRepository, "commit", commit_and_cancel):
            summary = app.run(
                RunRequest(
                    repo_path=repo,
                    remote_url=str(remote),
                    branch="main",
                    collaborators=(Collaborator("Ada Lovelace", "ada", "ada@example.com", 2),),
                    assume_yes=True,
                    non_interactive=True,
                )
            )

        self.assertTrue(summary.aborted)
        self.assertEqual(summary.commits_created, 1)
        self.assertEqual(summary.pushes, 0)
        self.assertEqual(self.git_stdout("rev-list", "--count", "main").strip(), "1")

        state = StateStore.in_git_dir(GitRepository(repo, logger=null_logger("git")).git_dir).load()
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.completed, frozenset({0}))
        self.assertEqual(state.pushed, -1)

    def test_cancellation_during_planning_leaves_repository_untouched(self) -> None:
        repo = self.init_repo()
        self.write("a.py", "print('a')\n")
        self.write("b.py", "print('b')\n")
        remote = self.init_bare_remote()

        event = threading.Event()

        class CancellingFactory:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, files: list[str]) -> str:
                self.calls += 1
                event.set()
                return "feat: mensaje"

        factory = CancellingFactory()
        app = self.make_app(Settings(), message_factory=factory, cancelled=event.is_set)
        summary = app.run(
            RunRequest(
                repo_path=repo,
                remote_url=str(remote),
                branch="main",
                collaborators=(Collaborator("Ada Lovelace", "ada", "ada@example.com", 2),),
                assume_yes=True,
                non_interactive=True,
            )
        )

        self.assertTrue(summary.aborted)
        self.assertEqual(factory.calls, 1)
        self.assertNotEqual(self.git("rev-parse", "--verify", "HEAD").returncode, 0)
        self.assertFalse((repo / ".git" / "autocommit" / "state.json").exists())

    def test_delay_reacts_to_cancellation(self) -> None:
        event = threading.Event()
        app = Application(
            Settings(),
            ScriptedPrompter(),
            TokenProvider(
                FileTokenStore(self.tmp / "tokens.json"),
                ScriptedPrompter(),
                logger=null_logger("tokens"),
            ),
            logger=null_logger("app"),
            cancelled=event.is_set,
        )
        event.set()
        started = time.monotonic()
        app._delay(5)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_dry_run_without_remote(self) -> None:
        repo = self.init_repo()
        self.write("a.py", "print('a')\n")
        app = self.make_app(Settings(dry_run=True))
        summary = app.run(
            RunRequest(
                repo_path=repo,
                branch="main",
                collaborators=(Collaborator("Ada", "ada", "ada@example.com", 1),),
                assume_yes=True,
                non_interactive=True,
            )
        )
        self.assertTrue(summary.ok)
        self.assertEqual(summary.commits_created, 0)
        self.assertEqual(self.git_stdout("remote").strip(), "")

    def test_aborts_when_user_declines(self) -> None:
        repo = self.init_repo()
        self.write("src/a.py", "print('a')\n")
        app = self.make_app(Settings(), ScriptedPrompter(confirmations=[False]))

        summary = app.run(
            RunRequest(
                repo_path=repo,
                remote_url=str(self.tmp / "remote.git"),
                branch="main",
                collaborators=(Collaborator("Ada", "ada", "ada@example.com", 1),),
            )
        )

        self.assertTrue(summary.aborted)
        self.assertEqual(summary.commits_created, 0)
        self.assertNotEqual(self.git("rev-parse", "--verify", "HEAD").returncode, 0)

    def test_requires_repo_in_non_interactive_mode(self) -> None:
        app = self.make_app(Settings(dry_run=True))
        with self.assertRaises(ValidationError):
            app.run(RunRequest(non_interactive=True))

    def test_requires_collaborators_in_non_interactive_mode(self) -> None:
        repo = self.init_repo()
        self.write("a.py", "print('a')\n")
        app = self.make_app(Settings(dry_run=True))
        with self.assertRaises(ValidationError):
            app.run(
                RunRequest(
                    repo_path=repo,
                    remote_url=str(self.tmp / "remote.git"),
                    branch="main",
                    non_interactive=True,
                )
            )

    def test_rejects_more_commits_than_files(self) -> None:
        repo = self.init_repo()
        self.write("a.py", "print('a')\n")
        app = self.make_app(Settings(dry_run=True))
        with self.assertRaises(ValidationError):
            app.run(
                RunRequest(
                    repo_path=repo,
                    remote_url=str(self.tmp / "remote.git"),
                    branch="main",
                    collaborators=(Collaborator("Ada", "ada", "ada@example.com", 2),),
                    assume_yes=True,
                    non_interactive=True,
                )
            )

    def test_strips_credentials_from_existing_remote(self) -> None:
        repo = self.init_repo()
        self.write("a.py", "print('a')\n")
        self.git(
            "remote",
            "add",
            "origin",
            f"https://{TOKEN}@github.com/ada/repo.git",
        )
        app = self.make_app(Settings(dry_run=True))

        app.run(
            RunRequest(
                repo_path=repo,
                branch="main",
                collaborators=(Collaborator("Ada", "ada", "ada@example.com", 1),),
                assume_yes=True,
                non_interactive=True,
            )
        )

        # En dry-run no se modifica el repositorio, ni siquiera el remoto.
        config = (repo / ".git" / "config").read_text(encoding="utf-8")
        self.assertIn(TOKEN, config)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from autocommit.errors import GitError, ValidationError
from autocommit.git_repository import (
    GitRepository,
    Identity,
    PushCredentials,
    normalize_remote_url,
    strip_credentials,
)
from tests.support import TemporaryProjectTestCase, null_logger

TOKEN = "ghp_" + "c" * 36


class RemoteUrlTests(unittest.TestCase):
    def test_strips_credentials(self) -> None:
        self.assertEqual(
            strip_credentials("https://user:token@github.com/ada/repo.git"),
            "https://github.com/ada/repo.git",
        )
        self.assertEqual(
            strip_credentials("https://token@github.com/ada/repo.git"),
            "https://github.com/ada/repo.git",
        )

    def test_keeps_clean_urls(self) -> None:
        self.assertEqual(
            normalize_remote_url("https://github.com/ada/repo.git"),
            "https://github.com/ada/repo.git",
        )
        self.assertEqual(
            normalize_remote_url("git@github.com:ada/repo.git"),
            "git@github.com:ada/repo.git",
        )

    def test_rejects_invalid_urls(self) -> None:
        for url in ("", "   ", "ftp://example.com/repo.git", "https://example.com/a b"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                normalize_remote_url(url)


class GitRepositoryTests(TemporaryProjectTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_repo()
        self.repo = GitRepository(self.tmp, logger=null_logger("git"))

    def test_detects_initialized_repository(self) -> None:
        self.assertTrue(self.repo.is_initialized())

    def test_init_is_idempotent(self) -> None:
        self.repo.init()
        self.assertTrue(self.repo.is_initialized())

    def test_git_dir_is_absolute(self) -> None:
        self.assertTrue(self.repo.git_dir.is_absolute())

    def test_ensure_branch_on_unborn_repository(self) -> None:
        self.repo.ensure_branch("main")
        self.assertEqual(self.repo.current_branch(), "main")
        self.assertFalse(self.repo.has_commits())

    def test_ensure_branch_creates_and_switches(self) -> None:
        self.repo.ensure_branch("main")
        self.repo.set_identity(Identity("Test", "test@example.com"))
        self.write("a.py", "print('a')\n")
        self.repo.stage(["a.py"])
        self.repo.commit("feat: add a")
        self.repo.ensure_branch("feature")
        self.assertEqual(self.repo.current_branch(), "feature")

    def test_identity_is_restored(self) -> None:
        self.assertIsNone(self.repo.identity())
        snapshot = self.repo.capture_identity()
        self.repo.set_identity(Identity("Ada", "ada@example.com"))
        self.assertEqual(self.repo.identity(), Identity("Ada", "ada@example.com"))
        self.repo.restore_identity(snapshot)
        self.assertIsNone(self.repo.identity())

    def test_restores_existing_identity(self) -> None:
        self.repo.set_identity(Identity("Original", "original@example.com"))
        snapshot = self.repo.capture_identity()
        self.repo.set_identity(Identity("Otro", "otro@example.com"))
        self.repo.restore_identity(snapshot)
        self.assertEqual(self.repo.identity(), Identity("Original", "original@example.com"))

    def test_stage_detects_changes(self) -> None:
        self.write("a.py", "print('a')\n")
        self.repo.stage(["a.py"])
        self.assertTrue(self.repo.has_staged_changes())

    def test_commit_returns_sha(self) -> None:
        self.repo.ensure_branch("main")
        self.repo.set_identity(Identity("Test", "test@example.com"))
        self.write("a.py", "print('a')\n")
        self.repo.stage(["a.py"])
        self.repo.commit("feat: add a")
        sha = self.git_stdout("rev-parse", "HEAD").strip()
        self.assertEqual(len(sha), 40)

    def test_stage_requires_files(self) -> None:
        with self.assertRaises(ValidationError):
            self.repo.stage([])

    def test_push_does_not_leak_token_into_config(self) -> None:
        self.repo.ensure_branch("main")
        self.repo.set_identity(Identity("Test", "test@example.com"))
        self.write("a.py", "print('a')\n")
        self.repo.stage(["a.py"])
        self.repo.commit("feat: add a")

        remote = self.init_bare_remote()
        self.repo.add_remote("origin", str(remote))
        self.repo.push("main", PushCredentials("ada", TOKEN))

        remote_log = self.git_stdout("log", "--format=%s", "main", cwd=remote).strip()
        self.assertEqual(remote_log, "feat: add a")

        config = (self.tmp / ".git" / "config").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, config)
        self.assertNotIn("ghp_", config)

    def test_push_requires_token_and_branch(self) -> None:
        with self.assertRaises(ValidationError):
            self.repo.push("main", PushCredentials("ada", " "))
        with self.assertRaises(ValidationError):
            self.repo.push("  ", PushCredentials("ada", TOKEN))

    def test_add_remote_twice_fails(self) -> None:
        remote = self.init_bare_remote()
        self.repo.add_remote("origin", str(remote))
        with self.assertRaises(ValidationError):
            self.repo.add_remote("origin", str(remote))

    def test_fetch_without_remote_returns_false(self) -> None:
        self.assertFalse(self.repo.fetch("origin"))

    def test_missing_remote_url_is_none(self) -> None:
        self.assertIsNone(self.repo.remote_url("origin"))

    def test_remote_branch_tracking(self) -> None:
        # Crea un remoto con la rama main y comprueba que se puede rastrear.
        remote = self.init_bare_remote()
        self.repo.ensure_branch("main")
        self.repo.set_identity(Identity("Test", "test@example.com"))
        self.write("a.py", "print('a')\n")
        self.repo.stage(["a.py"])
        self.repo.commit("feat: add a")
        self.repo.add_remote("origin", str(remote))
        self.repo.push("main", PushCredentials("ada", TOKEN))

        clone_root = self.tmp / "clone"
        result = self.git("clone", "-q", str(remote), str(clone_root))
        self.assertEqual(result.returncode, 0, result.stderr)
        clone = GitRepository(clone_root, logger=null_logger("git-clone"))
        self.assertIn("main", clone.remote_branches("origin"))
        clone.ensure_branch("main")
        self.assertEqual(clone.current_branch(), "main")


class GitErrorTests(unittest.TestCase):
    def test_message_includes_stderr(self) -> None:
        error = GitError("fallo", returncode=1, stderr="detalle")
        self.assertIn("detalle", str(error))


if __name__ == "__main__":
    unittest.main()

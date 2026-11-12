from __future__ import annotations

import unittest

from autocommit.errors import GitError
from autocommit.runner import CommandRunner
from tests.support import TemporaryProjectTestCase

TOKEN = "ghp_" + "b" * 36


class CommandRunnerTests(TemporaryProjectTestCase):
    def test_runs_git(self) -> None:
        runner = CommandRunner(self.tmp)
        result = runner.git("--version")
        self.assertTrue(result.ok)
        self.assertIn("git version", result.stdout)

    def test_passes_stdin(self) -> None:
        runner = CommandRunner(self.tmp)
        result = runner.run(["cat"], stdin="hola")
        self.assertEqual(result.stdout, "hola")

    def test_failure_raises_git_error(self) -> None:
        runner = CommandRunner(self.tmp)
        with self.assertRaises(GitError) as context:
            runner.git("rev-parse", "--verify", "no-existe")
        self.assertNotEqual(context.exception.returncode, 0)

    def test_redacts_credentials_in_errors(self) -> None:
        runner = CommandRunner(self.tmp)
        script = f'printf "%s\\n" "fatal: https://{TOKEN}@github.com/ada/repo.git" >&2; exit 1'
        with self.assertRaises(GitError) as context:
            runner.run(["sh", "-c", script])
        message = str(context.exception)
        self.assertNotIn(TOKEN, message)
        self.assertIn("***", message)

    def test_missing_executable_is_reported(self) -> None:
        runner = CommandRunner(self.tmp)
        with self.assertRaises(GitError) as context:
            runner.run(["comando-que-no-existe"])
        self.assertIn("no se encontró", str(context.exception).lower())

    def test_check_false_returns_result(self) -> None:
        runner = CommandRunner(self.tmp)
        result = runner.run(["sh", "-c", "exit 3"], check=False)
        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 3)


if __name__ == "__main__":
    unittest.main()

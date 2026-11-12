from __future__ import annotations

import random
import unittest

from autocommit.errors import ValidationError
from autocommit.messages import ConventionalCommitMessageFactory
from autocommit.models import Collaborator
from autocommit.planner import CommitPlanner
from autocommit.settings import Settings


def factory(seed: int = 0) -> ConventionalCommitMessageFactory:
    return ConventionalCommitMessageFactory(random.Random(seed))


def plan_for(
    files: list[str],
    collaborators: list[Collaborator],
    settings: Settings | None = None,
):
    planner = CommitPlanner(settings or Settings(), factory())
    return planner.build(files, collaborators, "main")


class DistributionTests(unittest.TestCase):
    def test_splits_files_evenly(self) -> None:
        files = [f"file_{index}.py" for index in range(5)]
        collaborator = Collaborator("Ada", "ada", "ada@example.com", 2)
        plan = plan_for(files, [collaborator])
        self.assertEqual([len(commit.files) for commit in plan.commits], [3, 2])
        self.assertEqual(plan.files, tuple(files))
        self.assertEqual(plan.total_files, 5)

    def test_respects_collaborator_order_and_counts(self) -> None:
        files = [f"file_{index}.py" for index in range(4)]
        ada = Collaborator("Ada", "ada", "ada@example.com", 2)
        alan = Collaborator("Alan", "alan", "alan@example.com", 2)
        plan = plan_for(files, [ada, alan])
        self.assertEqual(
            [commit.collaborator.username for commit in plan.commits],
            ["ada", "ada", "alan", "alan"],
        )
        self.assertEqual([commit.index for commit in plan.commits], [0, 1, 2, 3])

    def test_respects_max_files_per_commit(self) -> None:
        files = [f"file_{index}.py" for index in range(250)]
        collaborator = Collaborator("Ada", "ada", "ada@example.com", 3)
        plan = plan_for(files, [collaborator], Settings(max_files_per_commit=100))
        self.assertTrue(all(len(commit.files) <= 100 for commit in plan.commits))
        self.assertEqual(sum(len(commit.files) for commit in plan.commits), 250)

    def test_every_commit_has_files(self) -> None:
        files = [f"file_{index}.py" for index in range(3)]
        collaborator = Collaborator("Ada", "ada", "ada@example.com", 3)
        plan = plan_for(files, [collaborator])
        self.assertTrue(all(commit.files for commit in plan.commits))

    def test_no_file_is_repeated(self) -> None:
        files = [f"file_{index}.py" for index in range(7)]
        collaborators = [
            Collaborator("Ada", "ada", "ada@example.com", 3),
            Collaborator("Alan", "alan", "alan@example.com", 2),
        ]
        plan = plan_for(files, collaborators)
        self.assertEqual(len(plan.files), len(set(plan.files)))
        self.assertEqual(set(plan.files), set(files))


class ValidationTests(unittest.TestCase):
    def test_requires_files(self) -> None:
        with self.assertRaises(ValidationError):
            plan_for([], [Collaborator("Ada", "ada", "ada@example.com", 1)])

    def test_requires_collaborators(self) -> None:
        with self.assertRaises(ValidationError):
            plan_for(["a.py"], [])

    def test_requires_at_least_one_commit(self) -> None:
        with self.assertRaises(ValidationError):
            plan_for(["a.py"], [Collaborator("Ada", "ada", "ada@example.com", 0)])

    def test_rejects_more_commits_than_files(self) -> None:
        with self.assertRaises(ValidationError) as context:
            plan_for(["a.py"], [Collaborator("Ada", "ada", "ada@example.com", 3)])
        self.assertIn("al menos un archivo", str(context.exception))

    def test_rejects_plan_that_exceeds_file_limit(self) -> None:
        files = [f"file_{index}.py" for index in range(250)]
        with self.assertRaises(ValidationError) as context:
            plan_for(
                files,
                [Collaborator("Ada", "ada", "ada@example.com", 2)],
                Settings(max_files_per_commit=100),
            )
        self.assertIn("al menos 3 commits", str(context.exception))

    def test_requires_branch(self) -> None:
        planner = CommitPlanner(Settings(), factory())
        with self.assertRaises(ValidationError):
            planner.build(["a.py"], [Collaborator("Ada", "ada", "ada@example.com", 1)], "  ")

    def test_required_commits_helper(self) -> None:
        self.assertEqual(CommitPlanner.required_commits(1, 100), 1)
        self.assertEqual(CommitPlanner.required_commits(100, 100), 1)
        self.assertEqual(CommitPlanner.required_commits(101, 100), 2)
        self.assertEqual(CommitPlanner.required_commits(250, 100), 3)


class MessageIntegrationTests(unittest.TestCase):
    def test_messages_are_conventional(self) -> None:
        files = [f"src/module_{index}.py" for index in range(4)]
        plan = plan_for(files, [Collaborator("Ada", "ada", "ada@example.com", 2)])
        for commit in plan.commits:
            self.assertRegex(commit.message, r"^(feat|fix|docs|test|style|chore)")


if __name__ == "__main__":
    unittest.main()

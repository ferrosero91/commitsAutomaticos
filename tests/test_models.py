from __future__ import annotations

import unittest

from autocommit.errors import ValidationError
from autocommit.models import Collaborator, CommitPlan, PlannedCommit


class CollaboratorTests(unittest.TestCase):
    def test_normalizes_whitespace(self) -> None:
        collaborator = Collaborator("  Ada Lovelace  ", " ada ", " ada@example.com ", 2)
        self.assertEqual(collaborator.name, "Ada Lovelace")
        self.assertEqual(collaborator.username, "ada")
        self.assertEqual(collaborator.email, "ada@example.com")
        self.assertEqual(collaborator.identity, ("Ada Lovelace", "ada@example.com"))

    def test_rejects_empty_name(self) -> None:
        with self.assertRaises(ValidationError):
            Collaborator("   ", "ada", "ada@example.com")

    def test_rejects_invalid_username(self) -> None:
        for username in ("", "ada lovelace", "-ada", "ada_", "a" * 40):
            with self.subTest(username=username), self.assertRaises(ValidationError):
                Collaborator("Ada", username, "ada@example.com")

    def test_rejects_invalid_email(self) -> None:
        for email in ("", "ada", "ada@", "@example.com", "ada@example"):
            with self.subTest(email=email), self.assertRaises(ValidationError):
                Collaborator("Ada", "ada", email)

    def test_rejects_negative_commits(self) -> None:
        with self.assertRaises(ValidationError):
            Collaborator("Ada", "ada", "ada@example.com", -1)

    def test_accepts_zero_commits(self) -> None:
        self.assertEqual(Collaborator("Ada", "ada", "ada@example.com", 0).commits, 0)


class PlannedCommitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.collaborator = Collaborator("Ada", "ada", "ada@example.com", 1)

    def test_requires_files(self) -> None:
        with self.assertRaises(ValidationError):
            PlannedCommit(0, self.collaborator, (), "feat: algo")

    def test_requires_message(self) -> None:
        with self.assertRaises(ValidationError):
            PlannedCommit(0, self.collaborator, ("a.py",), "   ")

    def test_rejects_negative_index(self) -> None:
        with self.assertRaises(ValidationError):
            PlannedCommit(-1, self.collaborator, ("a.py",), "feat: algo")


class CommitPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ada = Collaborator("Ada", "ada", "ada@example.com", 2)
        self.alan = Collaborator("Alan", "alan", "alan@example.com", 1)
        self.first = PlannedCommit(0, self.ada, ("a.py",), "feat: a")
        self.second = PlannedCommit(1, self.ada, ("b.py", "c.py"), "feat: b")
        self.third = PlannedCommit(2, self.alan, ("d.py",), "chore: d")

    def test_exposes_derived_data(self) -> None:
        plan = CommitPlan("main", (self.first, self.second, self.third), 4)
        self.assertEqual(len(plan), 3)
        self.assertEqual(plan.total_commits, 3)
        self.assertEqual(plan.total_files, 4)
        self.assertEqual(plan.collaborators, (self.ada, self.alan))
        self.assertEqual(plan.collaborator_count, 2)
        self.assertEqual(plan.files, ("a.py", "b.py", "c.py", "d.py"))

    def test_pending_filters_completed(self) -> None:
        plan = CommitPlan("main", (self.first, self.second, self.third), 4)
        self.assertEqual(plan.pending({0, 2}), (self.second,))

    def test_rejects_duplicate_indices(self) -> None:
        with self.assertRaises(ValidationError):
            CommitPlan("main", (self.first, self.first), 1)

    def test_rejects_out_of_range_indices(self) -> None:
        with self.assertRaises(ValidationError):
            CommitPlan("main", (self.third,), 1)

    def test_rejects_empty_branch(self) -> None:
        with self.assertRaises(ValidationError):
            CommitPlan("  ", (self.first,), 1)


if __name__ == "__main__":
    unittest.main()

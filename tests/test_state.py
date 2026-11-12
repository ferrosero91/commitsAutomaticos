from __future__ import annotations

import json
import random
import unittest

from autocommit.errors import StateError
from autocommit.messages import ConventionalCommitMessageFactory
from autocommit.models import Collaborator
from autocommit.planner import CommitPlanner
from autocommit.settings import Settings
from autocommit.state import StateStore
from tests.support import TemporaryProjectTestCase


class StateStoreTests(TemporaryProjectTestCase):
    def setUp(self) -> None:
        super().setUp()
        planner = CommitPlanner(Settings(), ConventionalCommitMessageFactory(random.Random(3)))
        self.files = [f"file_{index}.py" for index in range(4)]
        self.collaborators = (
            Collaborator("Ada", "ada", "ada@example.com", 2),
            Collaborator("Alan", "alan", "alan@example.com", 1),
        )
        self.plan = planner.build(self.files, self.collaborators, "main")
        self.store = StateStore(self.tmp / "state" / "state.json")

    def test_load_without_state_returns_none(self) -> None:
        self.assertIsNone(self.store.load())

    def test_roundtrip(self) -> None:
        self.store.save(self.plan, {0})
        state = self.store.load()
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.plan, self.plan)
        self.assertEqual(state.completed, frozenset({0}))
        self.assertEqual(state.pushed, -1)
        self.assertTrue(state.has_unpushed_commits)
        self.assertEqual([commit.index for commit in state.pending()], [1, 2])

    def test_pushed_progress_roundtrip(self) -> None:
        self.store.save(self.plan, {0, 1, 2}, pushed=2)
        state = self.store.load()
        assert state is not None
        self.assertEqual(state.pushed, 2)
        self.assertFalse(state.has_unpushed_commits)

    def test_pushed_is_clamped_to_plan(self) -> None:
        self.store.save(self.plan, {0}, pushed=99)
        state = self.store.load()
        assert state is not None
        self.assertEqual(state.pushed, self.plan.total_commits - 1)

    def test_does_not_persist_tokens(self) -> None:
        self.store.save(self.plan, set())
        contents = self.store.path.read_text(encoding="utf-8")
        self.assertNotIn("token", contents.lower())

    def test_is_complete(self) -> None:
        self.store.save(self.plan, {0, 1, 2})
        state = self.store.load()
        assert state is not None
        self.assertTrue(state.is_complete)

    def test_ignores_unknown_completed_indices(self) -> None:
        self.store.save(self.plan, {0, 99})
        state = self.store.load()
        assert state is not None
        self.assertEqual(state.completed, frozenset({0}))

    def test_clear_removes_file(self) -> None:
        self.store.save(self.plan, set())
        self.assertTrue(self.store.exists())
        self.store.clear()
        self.assertFalse(self.store.exists())

    def test_corrupt_state_raises(self) -> None:
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text("{no es json", encoding="utf-8")
        with self.assertRaises(StateError):
            self.store.load()

    def test_incompatible_version_raises(self) -> None:
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text(json.dumps({"version": 999, "commits": []}), encoding="utf-8")
        with self.assertRaises(StateError):
            self.store.load()

    def test_malformed_payload_raises(self) -> None:
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text(json.dumps({"version": 1, "branch": "main"}), encoding="utf-8")
        with self.assertRaises(StateError):
            self.store.load()

    def test_state_file_permissions(self) -> None:
        import stat

        self.store.save(self.plan, set())
        mode = stat.S_IMODE(self.store.path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_in_git_dir_helper(self) -> None:
        store = StateStore.in_git_dir(self.tmp / ".git")
        self.assertEqual(store.path, self.tmp / ".git" / "autocommit" / "state.json")


if __name__ == "__main__":
    unittest.main()

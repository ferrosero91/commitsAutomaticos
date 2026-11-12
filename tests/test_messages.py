from __future__ import annotations

import random
import re
import unittest

from autocommit.messages import (
    MAX_SUBJECT_LENGTH,
    ConventionalCommitMessageFactory,
    classify,
)

CONVENTIONAL_RE = re.compile(r"^(feat|fix|docs|test|style|chore)(\([a-z0-9-]+\))?: \S.*$")


class ClassificationTests(unittest.TestCase):
    def test_classifies_by_path_and_extension(self) -> None:
        cases = {
            "tests/test_app.py": "test",
            "src/app.test.ts": "test",
            "README.md": "docs",
            "docs/arquitectura.rst": "docs",
            "styles/main.scss": "style",
            "pyproject.toml": "chore",
            "Dockerfile": "chore",
            "src/App.tsx": "feat",
            "src/main.py": "feat",
            "assets/logo.bin": "chore",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify(path), expected)


class MessageFactoryTests(unittest.TestCase):
    def factory(self, seed: int = 0) -> ConventionalCommitMessageFactory:
        return ConventionalCommitMessageFactory(random.Random(seed))

    def test_single_code_file_uses_conventional_format(self) -> None:
        message = self.factory().create(["src/app.py"])
        self.assertRegex(message, CONVENTIONAL_RE)
        self.assertIn("app", message)

    def test_multiple_files_use_conventional_format(self) -> None:
        message = self.factory().create(["src/a.py", "src/b.py", "src/c.py"])
        self.assertRegex(message, CONVENTIONAL_RE)
        self.assertLessEqual(len(message), MAX_SUBJECT_LENGTH)

    def test_tests_get_test_type(self) -> None:
        message = self.factory().create(["tests/test_a.py", "tests/test_b.py"])
        self.assertTrue(message.startswith("test"), message)

    def test_docs_get_docs_type(self) -> None:
        message = self.factory().create(["README.md", "docs/guía.md"])
        self.assertTrue(message.startswith("docs"), message)

    def test_unknown_extension_gets_chore_type(self) -> None:
        message = self.factory().create(["data/archivo.xyz"])
        self.assertTrue(message.startswith("chore"), message)

    def test_mixed_group_prefers_feat(self) -> None:
        message = self.factory().create(["src/app.py", "styles/main.css", "README.md"])
        self.assertTrue(message.startswith("feat"), message)

    def test_scope_uses_common_directory(self) -> None:
        message = self.factory().create(["autocommit/app.py", "autocommit/cli.py"])
        self.assertIn("(autocommit)", message)

    def test_no_scope_for_root_files(self) -> None:
        message = self.factory().create(["app.py", "cli.py"])
        self.assertNotIn("(", message)

    def test_is_deterministic_with_seed(self) -> None:
        files = ["src/a.py", "src/b.py", "src/c.py"]
        first = self.factory(seed=42).create(files)
        second = self.factory(seed=42).create(files)
        self.assertEqual(first, second)

    def test_varies_without_seed(self) -> None:
        files = [f"src/file_{index}.py" for index in range(6)]
        messages = {self.factory(seed).create(files) for seed in range(12)}
        self.assertGreater(len(messages), 1)

    def test_truncates_long_subjects(self) -> None:
        long_name = "a" * 200
        message = self.factory().create([f"src/{long_name}.py"])
        self.assertLessEqual(len(message), MAX_SUBJECT_LENGTH)

    def test_requires_files(self) -> None:
        with self.assertRaises(ValueError):
            self.factory().create([])

    def test_never_ends_with_period(self) -> None:
        message = self.factory().create(["src/app.py"])
        self.assertFalse(message.endswith("."))


if __name__ == "__main__":
    unittest.main()

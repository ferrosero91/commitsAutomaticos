from __future__ import annotations

import unittest

from autocommit.files import FileDiscovery, GitIgnoreFilter
from autocommit.runner import CommandRunner
from tests.support import TemporaryProjectTestCase


class FileDiscoveryTests(TemporaryProjectTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_repo()
        self.write(".gitignore", "*.log\nsecret/\nbuild/\n")
        self.write("src/app.py", "print('app')\n")
        self.write("src/util.js", "export const x = 1;\n")
        self.write("docs/guía.md", "# Guia\n")
        self.write("debug.log", "traza\n")
        self.write("secret/keys.txt", "nada\n")
        self.write("build/out.bin", "binario\n")
        self.write("src/rebuild/app.py", "print('rebuild')\n")
        self.write(".github/workflows/ci.yml", "name: ci\n")
        self.write("README.md", "# Proyecto\n")
        self.write("node_modules/pkg/index.js", "module.exports = {};\n")
        self.write(".DS_Store", "basura")

    def discover(self) -> list[str]:
        runner = CommandRunner(self.tmp)
        discovery = FileDiscovery(self.tmp, GitIgnoreFilter(runner))
        return list(discovery.discover().files)

    def test_includes_valid_files(self) -> None:
        files = self.discover()
        self.assertIn("src/app.py", files)
        self.assertIn("src/util.js", files)
        self.assertIn("docs/guía.md", files)
        self.assertIn("README.md", files)

    def test_does_not_skip_dot_github_directory(self) -> None:
        self.assertIn(".github/workflows/ci.yml", self.discover())

    def test_respects_gitignore_patterns(self) -> None:
        files = self.discover()
        self.assertNotIn("debug.log", files)
        self.assertNotIn("secret/keys.txt", files)
        self.assertNotIn("build/out.bin", files)

    def test_gitignore_does_not_match_substrings(self) -> None:
        self.assertIn("src/rebuild/app.py", self.discover())

    def test_skips_excluded_directories_and_names(self) -> None:
        files = self.discover()
        self.assertNotIn("node_modules/pkg/index.js", files)
        self.assertNotIn(".DS_Store", files)

    def test_result_is_sorted(self) -> None:
        files = self.discover()
        self.assertEqual(files, sorted(files))

    def test_reads_nested_gitignore(self) -> None:
        self.write("sub/.gitignore", "generated/\n")
        self.write("sub/generated/code.py", "print('x')\n")
        self.write("sub/keep.py", "print('keep')\n")
        files = self.discover()
        self.assertNotIn("sub/generated/code.py", files)
        self.assertIn("sub/keep.py", files)

    def test_reports_ignored_files(self) -> None:
        runner = CommandRunner(self.tmp)
        report = FileDiscovery(self.tmp, GitIgnoreFilter(runner)).discover()
        self.assertIn("secret/keys.txt", report.ignored)

    def test_works_without_ignore_filter(self) -> None:
        files = FileDiscovery(self.tmp).discover().files
        self.assertIn("secret/keys.txt", files)

    def test_skips_nested_repositories(self) -> None:
        self.write("vendor/library/.git/HEAD", "ref: refs/heads/main\n")
        self.write("vendor/library/code.py", "print('vendored')\n")
        self.write("vendor/keep.py", "print('keep')\n")
        files = self.discover()
        self.assertNotIn("vendor/library/code.py", files)
        self.assertIn("vendor/keep.py", files)

    def test_skips_bare_repositories(self) -> None:
        self.write("mirror.git/HEAD", "ref: refs/heads/main\n")
        self.write("mirror.git/config", "[core]\n")
        files = self.discover()
        self.assertFalse(any(path.startswith("mirror.git/") for path in files))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from autocommit.security import (
    FileValidator,
    SecretScanner,
    redact,
)
from tests.support import TemporaryProjectTestCase

GITHUB_TOKEN = "ghp_" + "a" * 36
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


class SecretScannerTests(TemporaryProjectTestCase):
    def test_detects_github_token(self) -> None:
        path = self.write("config.py", f'TOKEN = "{GITHUB_TOKEN}"\n')
        findings = SecretScanner().scan(path)
        self.assertTrue(findings)
        self.assertEqual(findings[0].pattern, "github-token")
        self.assertEqual(findings[0].line, 1)

    def test_detects_aws_key(self) -> None:
        path = self.write("settings.txt", f"aws = {AWS_KEY}\n")
        names = {finding.pattern for finding in SecretScanner().scan(path)}
        self.assertIn("aws-access-key", names)

    def test_detects_private_key(self) -> None:
        path = self.write(
            "deploy.txt",
            "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----\n",
        )
        names = {finding.pattern for finding in SecretScanner().scan(path)}
        self.assertIn("private-key", names)

    def test_detects_generic_credentials_in_code(self) -> None:
        path = self.write("app.py", 'api_key = "s3cr3tV4lu3"\n')
        names = {finding.pattern for finding in SecretScanner().scan(path)}
        self.assertIn("generic-secret", names)

    def test_ignores_placeholder_values(self) -> None:
        path = self.write("app.py", 'api_key = "your-api-key-here"\n')
        self.assertEqual(SecretScanner().scan(path), [])

    def test_ignores_binary_files(self) -> None:
        path = self.tmp / "blob.bin"
        path.write_bytes(b"\x00\x01" + GITHUB_TOKEN.encode())
        self.assertEqual(SecretScanner().scan(path), [])

    def test_ignores_generic_patterns_in_documentation(self) -> None:
        path = self.write("README.md", 'api_key = "realValue123"\n')
        self.assertEqual(SecretScanner().scan(path), [])


class FileValidatorTests(TemporaryProjectTestCase):
    def test_rejects_large_files(self) -> None:
        self.write("big.py", "x" * 64)
        validator = FileValidator(self.tmp, max_size_bytes=16)
        report = validator.validate(["big.py"])
        self.assertEqual(report.accepted, ())
        self.assertIn("tamaño máximo", report.rejected[0].reason)

    def test_rejects_sensitive_names(self) -> None:
        for name in (".env", "id_rsa", "server.pem", "credentials"):
            with self.subTest(name=name):
                self.write(name, "contenido")
                validator = FileValidator(self.tmp, max_size_bytes=1024)
                report = validator.validate([name])
                self.assertEqual(report.accepted, ())
                self.assertIn("sensible", report.rejected[0].reason)

    def test_rejects_files_with_secrets(self) -> None:
        self.write("config.py", f'TOKEN = "{GITHUB_TOKEN}"\n')
        validator = FileValidator(self.tmp, max_size_bytes=1024)
        report = validator.validate(["config.py"])
        self.assertEqual(report.accepted, ())
        self.assertIn("secreto", report.rejected[0].reason)

    def test_accepts_clean_files(self) -> None:
        self.write("src/app.py", "print('hola')\n")
        validator = FileValidator(self.tmp, max_size_bytes=1024)
        report = validator.validate(["src/app.py"])
        self.assertEqual(report.accepted, ("src/app.py",))
        self.assertEqual(report.rejected, ())

    def test_allow_secrets_skips_content_checks_but_not_size(self) -> None:
        self.write(".env", f"TOKEN={GITHUB_TOKEN}\n")
        self.write("big.py", "x" * 2048)
        validator = FileValidator(self.tmp, max_size_bytes=1024, allow_secrets=True)
        report = validator.validate([".env", "big.py"])
        self.assertEqual(report.accepted, (".env",))
        self.assertEqual(report.rejected_paths, ("big.py",))

    def test_unreadable_file_is_rejected(self) -> None:
        validator = FileValidator(self.tmp, max_size_bytes=1024)
        report = validator.validate(["no-existe.py"])
        self.assertEqual(report.accepted, ())
        self.assertIn("no se pudo leer", report.rejected[0].reason)


class RedactionTests(unittest.TestCase):
    def test_removes_credentials_from_urls(self) -> None:
        self.assertEqual(
            redact("https://user:token@github.com/ada/repo.git"),
            "https://***@github.com/ada/repo.git",
        )

    def test_removes_github_tokens(self) -> None:
        self.assertEqual(redact(f"token={GITHUB_TOKEN}"), "token=***")

    def test_keeps_clean_text(self) -> None:
        self.assertEqual(redact("todo correcto"), "todo correcto")
        self.assertEqual(redact(""), "")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path

from autocommit.env import load_env_file, parse_env_file
from autocommit.errors import ValidationError
from tests.support import TemporaryProjectTestCase


class ParseEnvFileTests(TemporaryProjectTestCase):
    def test_parses_values_comments_and_export(self) -> None:
        path = self.write(
            ".env",
            "# comentario\n"
            "\n"
            "OPENCODE_API_KEY=oc_sk_123\n"
            'export MODELO="deepseek-v4.1-flash"\n'
            "OTRA='con espacios'\n",
        )
        self.assertEqual(
            parse_env_file(path),
            {
                "OPENCODE_API_KEY": "oc_sk_123",
                "MODELO": "deepseek-v4.1-flash",
                "OTRA": "con espacios",
            },
        )

    def test_accepts_empty_values(self) -> None:
        path = self.write(".env", "VACIA=\n")
        self.assertEqual(parse_env_file(path), {"VACIA": ""})

    def test_rejects_invalid_line(self) -> None:
        path = self.write(".env", "SIN_IGUAL\n")
        with self.assertRaises(ValidationError) as context:
            parse_env_file(path)
        self.assertIn("línea 1", str(context.exception).lower())

    def test_rejects_invalid_name(self) -> None:
        path = self.write(".env", "1MAL=valor\n")
        with self.assertRaises(ValidationError):
            parse_env_file(path)

    def test_rejects_missing_file(self) -> None:
        with self.assertRaises(ValidationError):
            parse_env_file(self.tmp / "no-existe.env")

    def test_ignores_comments_and_blank_lines(self) -> None:
        path = self.write(".env", "\n# solo comentarios\n\n")
        self.assertEqual(parse_env_file(path), {})


class LoadEnvFileTests(TemporaryProjectTestCase):
    def test_does_not_override_existing(self) -> None:
        path = self.write(".env", "A=del-archivo\nB=del-archivo\n")
        environ = {"A": "del-entorno"}
        applied = load_env_file(path, environ=environ)
        self.assertEqual(environ, {"A": "del-entorno", "B": "del-archivo"})
        self.assertEqual(applied, {"B": "del-archivo"})

    def test_override_replaces_existing(self) -> None:
        path = self.write(".env", "A=del-archivo\n")
        environ = {"A": "del-entorno"}
        applied = load_env_file(path, environ=environ, override=True)
        self.assertEqual(environ, {"A": "del-archivo"})
        self.assertEqual(applied, {"A": "del-archivo"})

    def test_empty_existing_value_is_filled(self) -> None:
        path = self.write(".env", "A=valor\n")
        environ = {"A": ""}
        load_env_file(path, environ=environ)
        self.assertEqual(environ["A"], "valor")

    def test_accepts_arbitrary_mapping(self) -> None:
        path = self.write(".env", "A=1\n")
        environ: dict[str, str] = {}
        applied = load_env_file(path, environ=environ)
        self.assertEqual(applied, {"A": "1"})
        self.assertEqual(environ, {"A": "1"})

    def test_returns_path_compatible_with_pathlib(self) -> None:
        path = Path(self.write(".env", "A=1\n"))
        self.assertEqual(parse_env_file(path), {"A": "1"})


if __name__ == "__main__":
    unittest.main()

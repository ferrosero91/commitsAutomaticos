from __future__ import annotations

import unittest
from unittest import mock

from autocommit.errors import ValidationError
from autocommit.ui import (
    ConsolePrompter,
    NonInteractivePrompter,
    parse_collaborator,
)
from tests.support import ScriptedPrompter


class ParseCollaboratorTests(unittest.TestCase):
    def test_parses_valid_spec(self) -> None:
        collaborator = parse_collaborator("Ada Lovelace:ada:ada@example.com:3")
        self.assertEqual(collaborator.name, "Ada Lovelace")
        self.assertEqual(collaborator.username, "ada")
        self.assertEqual(collaborator.email, "ada@example.com")
        self.assertEqual(collaborator.commits, 3)

    def test_rejects_wrong_number_of_parts(self) -> None:
        with self.assertRaises(ValidationError):
            parse_collaborator("Ada:ada:ada@example.com")

    def test_rejects_non_numeric_commits(self) -> None:
        with self.assertRaises(ValidationError):
            parse_collaborator("Ada:ada:ada@example.com:muchos")

    def test_propagates_model_validation(self) -> None:
        with self.assertRaises(ValidationError):
            parse_collaborator("Ada:ada:no-es-email:1")


class ConsolePrompterTests(unittest.TestCase):
    def test_text_uses_default_on_empty_input(self) -> None:
        prompter = ConsolePrompter()
        with mock.patch("builtins.input", return_value=""):
            self.assertEqual(prompter.text("Nombre", default="main"), "main")

    def test_text_reprompts_when_required(self) -> None:
        prompter = ConsolePrompter()
        with mock.patch("builtins.input", side_effect=["", "valor"]), mock.patch("builtins.print"):
            self.assertEqual(prompter.text("Nombre"), "valor")

    def test_integer_reprompts_after_invalid_value(self) -> None:
        prompter = ConsolePrompter()
        with mock.patch("builtins.input", side_effect=["abc", "2"]), mock.patch("builtins.print"):
            self.assertEqual(prompter.integer("Commits", minimum=1), 2)

    def test_integer_respects_minimum(self) -> None:
        prompter = ConsolePrompter()
        with mock.patch("builtins.input", side_effect=["0", "4"]), mock.patch("builtins.print"):
            self.assertEqual(prompter.integer("Commits", minimum=1), 4)

    def test_confirm_accepts_spanish_and_english(self) -> None:
        prompter = ConsolePrompter()
        for answer in ("s", "Sí", "si", "y", "yes"):
            with self.subTest(answer=answer), mock.patch("builtins.input", return_value=answer):
                self.assertTrue(prompter.confirm("Continuar"))

    def test_confirm_rejects_negative_answers(self) -> None:
        prompter = ConsolePrompter()
        with mock.patch("builtins.input", return_value="no"):
            self.assertFalse(prompter.confirm("Continuar", default=True))

    def test_select_returns_index(self) -> None:
        prompter = ConsolePrompter()
        with mock.patch("builtins.input", return_value="2"), mock.patch("builtins.print"):
            self.assertEqual(prompter.select("Rama", ["main", "dev"]), 1)

    def test_select_can_return_none_when_empty(self) -> None:
        prompter = ConsolePrompter()
        with mock.patch("builtins.input", return_value=""), mock.patch("builtins.print"):
            self.assertIsNone(prompter.select("Rama", ["main"], allow_empty=True))


class NonInteractivePrompterTests(unittest.TestCase):
    def test_fails_instead_of_prompting(self) -> None:
        prompter = NonInteractivePrompter()
        with self.assertRaises(ValidationError):
            prompter.text("Nombre")
        with self.assertRaises(ValidationError):
            prompter.secret("Token")
        with self.assertRaises(ValidationError):
            prompter.integer("Commits")

    def test_uses_defaults_when_available(self) -> None:
        prompter = NonInteractivePrompter()
        self.assertEqual(prompter.text("Nombre", default="main"), "main")
        self.assertEqual(prompter.integer("Commits", default=3), 3)
        self.assertTrue(prompter.confirm("Continuar", default=True))


class ScriptedPrompterTests(unittest.TestCase):
    def test_returns_scripted_answers(self) -> None:
        prompter = ScriptedPrompter(
            texts=["Ada"], secrets=["ghp_x"], integers=[2], confirmations=[True]
        )
        self.assertEqual(prompter.text("Nombre"), "Ada")
        self.assertEqual(prompter.secret("Token"), "ghp_x")
        self.assertEqual(prompter.integer("Commits"), 2)
        self.assertTrue(prompter.confirm("Continuar"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import unittest
from unittest import mock

from autocommit.ai import default_key_file
from autocommit.credentials import FileTokenStore, read_secret_file, write_secret_file
from autocommit.errors import ValidationError
from autocommit.gui_state import (
    CollaboratorDraft,
    FormState,
    prepare_message_factory,
    prepare_token_provider,
    token_available,
    validate_form,
)
from tests.support import TemporaryProjectTestCase, null_logger


class GuiStateTests(TemporaryProjectTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._config = mock.patch.dict(
            os.environ, {"AUTOCOMMIT_CONFIG_DIR": str(self.tmp / "config")}, clear=True
        )
        self._config.start()
        self.addCleanup(self._config.stop)

    def make_state(self, **overrides: object) -> FormState:
        values: dict[str, object] = {
            "repo_path": self.tmp,
            "remote_url": "https://github.com/ada/repo.git",
            "branch": "main",
            "collaborators": [
                CollaboratorDraft(
                    name="Ada Lovelace",
                    username="ada",
                    email="ada@example.com",
                    commits=2,
                    token="ghp_token",
                )
            ],
        }
        values.update(overrides)
        return FormState(**values)  # type: ignore[arg-type]

    # -------------------------------------------------------------- validation

    def test_valid_state_has_no_errors(self) -> None:
        self.assertEqual(
            validate_form(self.make_state(), dry_run=False, token_lookup=lambda _user: False),
            [],
        )

    def test_requires_repo(self) -> None:
        errors = validate_form(
            self.make_state(repo_path=None), dry_run=True, token_lookup=lambda _u: True
        )
        self.assertTrue(any("carpeta" in error.lower() for error in errors))

    def test_requires_existing_repo(self) -> None:
        errors = validate_form(
            self.make_state(repo_path=self.tmp / "no-existe"),
            dry_run=True,
            token_lookup=lambda _u: True,
        )
        self.assertTrue(any("no existe" in error.lower() for error in errors))

    def test_requires_remote_only_for_real_run(self) -> None:
        state = self.make_state(remote_url="")
        self.assertEqual(validate_form(state, dry_run=True, token_lookup=lambda _u: True), [])
        self.assertTrue(
            any(
                "remoto" in error.lower()
                for error in validate_form(state, dry_run=False, token_lookup=lambda _u: True)
            )
        )

    def test_requires_collaborators_unless_resuming(self) -> None:
        state = self.make_state(collaborators=[])
        self.assertTrue(
            any(
                "colaborador" in error.lower()
                for error in validate_form(state, dry_run=True, token_lookup=lambda _u: True)
            )
        )
        self.assertEqual(
            validate_form(
                self.make_state(collaborators=[], resume=True),
                dry_run=True,
                token_lookup=lambda _u: True,
            ),
            [],
        )

    def test_reports_invalid_collaborator(self) -> None:
        errors = validate_form(
            self.make_state(
                collaborators=[
                    CollaboratorDraft(name="Ada", username="ada", email="no-es-email", commits=1)
                ]
            ),
            dry_run=True,
            token_lookup=lambda _u: True,
        )
        self.assertTrue(any("@ada" in error for error in errors))

    def test_reports_duplicate_usernames(self) -> None:
        state = self.make_state(
            collaborators=[
                CollaboratorDraft("Ada", "ada", "ada@example.com", 1),
                CollaboratorDraft("Otra Ada", "ada", "otra@example.com", 1),
            ]
        )
        self.assertTrue(
            any(
                "duplicado" in error
                for error in validate_form(state, dry_run=True, token_lookup=lambda _u: True)
            )
        )

    def test_requires_token_for_real_run(self) -> None:
        state = self.make_state(
            collaborators=[CollaboratorDraft("Ada", "ada", "ada@example.com", 1, token="")]
        )
        self.assertTrue(
            any(
                "token" in error.lower()
                for error in validate_form(state, dry_run=False, token_lookup=lambda _u: False)
            )
        )
        self.assertEqual(validate_form(state, dry_run=True, token_lookup=lambda _u: False), [])
        self.assertEqual(validate_form(state, dry_run=False, token_lookup=lambda _u: True), [])

    def test_reports_invalid_options(self) -> None:
        errors = validate_form(
            self.make_state(max_files_per_commit=0),
            dry_run=True,
            token_lookup=lambda _u: True,
        )
        self.assertTrue(any("max_files_per_commit" in error for error in errors))

    def test_ai_requires_key(self) -> None:
        errors = validate_form(
            self.make_state(ai_enabled=True),
            dry_run=True,
            token_lookup=lambda _u: True,
        )
        self.assertTrue(any("clave" in error.lower() for error in errors))

    def test_ai_accepts_key_from_environment(self) -> None:
        with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}, clear=False):
            self.assertEqual(
                validate_form(
                    self.make_state(ai_enabled=True),
                    dry_run=True,
                    token_lookup=lambda _u: True,
                ),
                [],
            )

    # ----------------------------------------------------------------- builders

    def test_to_settings(self) -> None:
        state = self.make_state(
            max_files_per_commit=50, push_every=0, delay_seconds=2.5, resume=True
        )
        settings = state.to_settings(dry_run=True)
        self.assertEqual(settings.max_files_per_commit, 50)
        self.assertEqual(settings.push_every, 0)
        self.assertEqual(settings.delay_seconds, 2.5)
        self.assertTrue(settings.resume)
        self.assertTrue(settings.dry_run)

    def test_to_settings_validates(self) -> None:
        with self.assertRaises(ValidationError):
            self.make_state(max_files_per_commit=0).to_settings()

    def test_to_request_is_non_interactive(self) -> None:
        request = self.make_state().to_request()
        self.assertTrue(request.assume_yes)
        self.assertTrue(request.non_interactive)
        self.assertEqual(len(request.collaborators), 1)
        self.assertEqual(request.collaborators[0].username, "ada")
        self.assertEqual(request.remote_url, "https://github.com/ada/repo.git")

    def test_to_request_uses_none_for_blank_optional_values(self) -> None:
        request = self.make_state(remote_url="  ", branch="").to_request()
        self.assertIsNone(request.remote_url)
        self.assertIsNone(request.branch)

    # ------------------------------------------------------------------- tokens

    def test_provider_uses_typed_token(self) -> None:
        provider = prepare_token_provider(self.make_state(), null_logger("gui"))
        self.assertEqual(provider.resolve("ada"), "ghp_token")

    def test_provider_saves_only_when_remembering(self) -> None:
        state = self.make_state(
            collaborators=[
                CollaboratorDraft(
                    "Ada", "ada", "ada@example.com", 1, token="ghp_nuevo", remember=True
                ),
                CollaboratorDraft(
                    "Alan", "alan", "alan@example.com", 1, token="ghp_alan", remember=False
                ),
            ]
        )
        prepare_token_provider(state, null_logger("gui"))
        store = FileTokenStore()
        self.assertEqual(store.get("ada"), "ghp_nuevo")
        self.assertIsNone(store.get("alan"))

    def test_provider_does_not_persist_on_preview(self) -> None:
        prepare_token_provider(self.make_state(), null_logger("gui"), persist=False)
        self.assertIsNone(FileTokenStore().get("ada"))

    def test_provider_falls_back_to_stored_token(self) -> None:
        FileTokenStore().set("ada", "ghp_guardado")
        state = self.make_state(
            collaborators=[CollaboratorDraft("Ada", "ada", "ada@example.com", 1, token="")]
        )
        provider = prepare_token_provider(state, null_logger("gui"))
        self.assertEqual(provider.resolve("ada"), "ghp_guardado")

    def test_provider_fails_without_token(self) -> None:
        state = self.make_state(
            collaborators=[CollaboratorDraft("Ada", "ada", "ada@example.com", 1, token="")]
        )
        provider = prepare_token_provider(state, null_logger("gui"))
        with self.assertRaises(ValidationError):
            provider.resolve("ada")

    def test_token_available_reads_environment_and_file(self) -> None:
        self.assertFalse(token_available("ada"))
        with mock.patch.dict(os.environ, {"AUTOCOMMIT_TOKEN_ADA": "ghp_env"}, clear=False):
            self.assertTrue(token_available("ada"))
        FileTokenStore().set("alan", "ghp_file")
        self.assertTrue(token_available("alan"))

    # ----------------------------------------------------------------------- ai

    def test_ai_factory_is_optional(self) -> None:
        self.assertIsNone(
            prepare_message_factory(
                self.make_state(ai_enabled=False), self.make_state().to_settings()
            )
        )

    def test_ai_factory_requires_key(self) -> None:
        with self.assertRaises(ValidationError):
            prepare_message_factory(
                self.make_state(ai_enabled=True),
                self.make_state().to_settings(),
                null_logger("gui"),
                persist=False,
            )

    def test_ai_factory_saves_typed_key(self) -> None:
        state = self.make_state(ai_enabled=True, ai_api_key="oc_sk_desde_gui")
        factory = prepare_message_factory(state, state.to_settings(), null_logger("gui"))
        self.assertIsNotNone(factory)
        self.assertEqual(read_secret_file(default_key_file()), "oc_sk_desde_gui")

    def test_ai_factory_does_not_save_on_preview(self) -> None:
        state = self.make_state(ai_enabled=True, ai_api_key="oc_sk_preview")
        prepare_message_factory(state, state.to_settings(), null_logger("gui"), persist=False)
        self.assertIsNone(read_secret_file(default_key_file()))

    def test_ai_factory_reads_environment_key(self) -> None:
        state = self.make_state(ai_enabled=True)
        with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_env"}, clear=False):
            factory = prepare_message_factory(
                state, state.to_settings(), null_logger("gui"), persist=False
            )
        self.assertIsNotNone(factory)

    def test_ai_factory_reads_saved_key(self) -> None:
        write_secret_file(default_key_file(), "oc_sk_archivo")
        state = self.make_state(ai_enabled=True)
        factory = prepare_message_factory(
            state, state.to_settings(), null_logger("gui"), persist=False
        )
        self.assertIsNotNone(factory)


if __name__ == "__main__":
    unittest.main()

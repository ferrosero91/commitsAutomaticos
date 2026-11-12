from __future__ import annotations

import os
import stat
import unittest
from unittest import mock

from autocommit.credentials import (
    ChainedTokenStore,
    EnvironmentTokenStore,
    FileTokenStore,
    TokenProvider,
)
from autocommit.errors import ValidationError
from tests.support import ScriptedPrompter, TemporaryProjectTestCase, null_logger


class FileTokenStoreTests(TemporaryProjectTestCase):
    def test_roundtrip(self) -> None:
        store = FileTokenStore(self.tmp / "config" / "tokens.json")
        self.assertIsNone(store.get("ada"))
        store.set("ada", "ghp_token")
        self.assertEqual(FileTokenStore(store.path).get("ada"), "ghp_token")

    def test_file_permissions_are_restrictive(self) -> None:
        store = FileTokenStore(self.tmp / "config" / "tokens.json")
        store.set("ada", "ghp_token")
        file_mode = stat.S_IMODE(store.path.stat().st_mode)
        directory_mode = stat.S_IMODE(store.path.parent.stat().st_mode)
        self.assertEqual(file_mode, 0o600)
        self.assertEqual(directory_mode, 0o700)

    def test_updates_existing_tokens(self) -> None:
        store = FileTokenStore(self.tmp / "tokens.json")
        store.set("ada", "uno")
        store.set("alan", "dos")
        store.set("ada", "tres")
        self.assertEqual(store.get("ada"), "tres")
        self.assertEqual(store.get("alan"), "dos")

    def test_corrupt_file_raises(self) -> None:
        path = self.tmp / "tokens.json"
        path.write_text("{no es json", encoding="utf-8")
        with self.assertRaises(ValidationError):
            FileTokenStore(path).get("ada")


class EnvironmentTokenStoreTests(unittest.TestCase):
    def test_reads_normalized_variable(self) -> None:
        store = EnvironmentTokenStore()
        with mock.patch.dict(os.environ, {"AUTOCOMMIT_TOKEN_ADA_LOVELACE": "ghp_env"}, clear=False):
            self.assertEqual(store.get("ada-lovelace"), "ghp_env")

    def test_missing_variable_returns_none(self) -> None:
        store = EnvironmentTokenStore()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(store.get("nadie"))

    def test_is_read_only(self) -> None:
        with self.assertRaises(ValidationError):
            EnvironmentTokenStore().set("ada", "ghp_token")


class ChainedTokenStoreTests(TemporaryProjectTestCase):
    def test_environment_wins_over_file(self) -> None:
        file_store = FileTokenStore(self.tmp / "tokens.json")
        file_store.set("ada", "ghp_file")
        chained = ChainedTokenStore(stores=(EnvironmentTokenStore(), file_store))
        with mock.patch.dict(os.environ, {"AUTOCOMMIT_TOKEN_ADA": "ghp_env"}, clear=False):
            self.assertEqual(chained.get("ada"), "ghp_env")
        self.assertEqual(chained.get("ada"), "ghp_file")

    def test_writes_to_first_writable_store(self) -> None:
        file_store = FileTokenStore(self.tmp / "tokens.json")
        chained = ChainedTokenStore(stores=(EnvironmentTokenStore(), file_store))
        chained.set("ada", "ghp_nuevo")
        self.assertEqual(file_store.get("ada"), "ghp_nuevo")


class TokenProviderTests(TemporaryProjectTestCase):
    def provider(self, prompter: ScriptedPrompter) -> tuple[TokenProvider, FileTokenStore]:
        store = FileTokenStore(self.tmp / "tokens.json")
        return TokenProvider(store, prompter, logger=null_logger("credentials")), store

    def test_uses_stored_token_without_prompting(self) -> None:
        store = FileTokenStore(self.tmp / "tokens.json")
        store.set("ada", "ghp_guardado")
        provider = TokenProvider(store, ScriptedPrompter(), logger=null_logger("credentials"))
        self.assertEqual(provider.resolve("ada"), "ghp_guardado")

    def test_prompts_and_saves_new_token(self) -> None:
        provider, store = self.provider(ScriptedPrompter(secrets=["  ghp_nuevo  "]))
        self.assertEqual(provider.resolve("ada"), "ghp_nuevo")
        self.assertEqual(store.get("ada"), "ghp_nuevo")

    def test_caches_resolved_tokens(self) -> None:
        prompter = ScriptedPrompter(secrets=["ghp_unico"])
        provider, _ = self.provider(prompter)
        provider.resolve("ada")
        self.assertEqual(provider.resolve("ada"), "ghp_unico")

    def test_rejects_empty_token(self) -> None:
        provider, _ = self.provider(ScriptedPrompter(secrets=["   "]))
        with self.assertRaises(ValidationError):
            provider.resolve("ada")

    def test_rejects_token_with_spaces(self) -> None:
        provider, _ = self.provider(ScriptedPrompter(secrets=["ghp token"]))
        with self.assertRaises(ValidationError):
            provider.resolve("ada")


if __name__ == "__main__":
    unittest.main()

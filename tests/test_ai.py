from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from autocommit import __version__
from autocommit.ai import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    AIConfig,
    AIMessageFactory,
    FileSnippetProvider,
    OpenCodeChatClient,
    build_user_prompt,
    resolve_api_key,
    sanitize_message,
)
from autocommit.cli import build_message_factory, build_parser, resolve_ai_config
from autocommit.credentials import read_secret_file, write_secret_file
from autocommit.env import load_env_file
from autocommit.errors import AIError, ValidationError
from autocommit.settings import Settings
from tests.support import ScriptedPrompter, TemporaryProjectTestCase, null_logger

GITHUB_TOKEN = "ghp_" + "a" * 36


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakeOpener:
    """Sustituye a urllib para inspeccionar la petición sin red."""

    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.requests: list[tuple[object, float | None]] = []

    def __call__(self, request: object, timeout: float | None = None) -> FakeResponse:
        self.requests.append((request, timeout))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, dict)
        return FakeResponse(result)


def completion(content: str | list[dict]) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def http_error(code: int, body: str = '{"error":"boom"}') -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.test", code, "error", None, io.BytesIO(body.encode("utf-8"))
    )


def config(**overrides: object) -> AIConfig:
    values = {
        "api_key": "oc_sk_test",
        "base_url": "https://example.test/openai/v1",
        "model": "deepseek-v4.1-flash",
    }
    values.update(overrides)
    return AIConfig(**values)  # type: ignore[arg-type]


class SanitizeMessageTests(unittest.TestCase):
    def test_accepts_clean_conventional_message(self) -> None:
        self.assertEqual(
            sanitize_message("feat(ui): añade panel de depósitos"),
            "feat(ui): añade panel de depósitos",
        )

    def test_removes_code_fences(self) -> None:
        self.assertEqual(sanitize_message("```\nfeat: añade panel\n```"), "feat: añade panel")

    def test_skips_prose_before_the_message(self) -> None:
        self.assertEqual(
            sanitize_message("Claro, aquí tienes:\nfeat: añade panel"),
            "feat: añade panel",
        )

    def test_removes_trailing_period_and_quotes(self) -> None:
        self.assertEqual(
            sanitize_message('"fix: corrige el desbordamiento."'),
            "fix: corrige el desbordamiento",
        )

    def test_removes_label_prefix(self) -> None:
        self.assertEqual(
            sanitize_message("Mensaje: docs: actualiza la guía"),
            "docs: actualiza la guía",
        )

    def test_rejects_non_conventional_output(self) -> None:
        self.assertIsNone(sanitize_message("He añadido un panel al proyecto."))

    def test_rejects_empty_output(self) -> None:
        self.assertIsNone(sanitize_message(""))
        self.assertIsNone(sanitize_message("   \n  "))

    def test_truncates_long_subject(self) -> None:
        message = sanitize_message("feat: " + "a" * 200)
        assert message is not None
        self.assertLessEqual(len(message), 72)

    def test_accepts_extended_conventional_types(self) -> None:
        for kind in ("refactor", "perf", "build", "ci"):
            with self.subTest(kind=kind):
                self.assertIsNotNone(sanitize_message(f"{kind}: cambia algo"))


class FileSnippetProviderTests(TemporaryProjectTestCase):
    def test_includes_file_content(self) -> None:
        self.write("src/a.py", "def hola():\n    return 1\n")
        context = FileSnippetProvider(self.tmp).build(["src/a.py"])
        self.assertIn("--- src/a.py ---", context)
        self.assertIn("def hola", context)

    def test_skips_binary_files(self) -> None:
        path = self.tmp / "blob.bin"
        path.write_bytes(b"\x00\x01contenido")
        self.assertEqual(FileSnippetProvider(self.tmp).build(["blob.bin"]), "")

    def test_redacts_tokens(self) -> None:
        self.write("config.py", f'TOKEN = "{GITHUB_TOKEN}"\n')
        context = FileSnippetProvider(self.tmp).build(["config.py"])
        self.assertNotIn(GITHUB_TOKEN, context)
        self.assertIn("***", context)

    def test_truncates_long_files(self) -> None:
        self.write("long.txt", "\n".join(f"linea {i}" for i in range(200)))
        context = FileSnippetProvider(self.tmp, max_lines=5).build(["long.txt"])
        self.assertNotIn("linea 10", context)
        self.assertIn("linea 4", context)

    def test_limits_total_size(self) -> None:
        for index in range(10):
            self.write(f"file_{index}.txt", "x" * 100)
        context = FileSnippetProvider(self.tmp, max_total_chars=250).build(
            [f"file_{index}.txt" for index in range(10)]
        )
        self.assertLessEqual(len(context), 300)

    def test_missing_file_is_ignored(self) -> None:
        self.assertEqual(FileSnippetProvider(self.tmp).build(["no-existe"]), "")


class PromptTests(unittest.TestCase):
    def test_includes_context_when_present(self) -> None:
        prompt = build_user_prompt(["a.py"], "--- a.py ---\ncontenido")
        self.assertIn("Extracto del contenido", prompt)
        self.assertIn("contenido", prompt)

    def test_lists_files(self) -> None:
        prompt = build_user_prompt(["src/a.py", "tests/test_a.py"])
        self.assertIn("- src/a.py", prompt)
        self.assertIn("- tests/test_a.py", prompt)

    def test_caps_long_list(self) -> None:
        files = [f"file_{index}.py" for index in range(100)]
        prompt = build_user_prompt(files, limit=10)
        self.assertIn("y 90 archivo(s) más", prompt)
        self.assertEqual(prompt.count("file_"), 10)


class AIConfigTests(unittest.TestCase):
    def test_builds_chat_completions_endpoint(self) -> None:
        self.assertEqual(config().endpoint, "https://example.test/openai/v1/chat/completions")

    def test_normalizes_trailing_slash(self) -> None:
        self.assertEqual(
            config(base_url="https://example.test/v1/").endpoint,
            "https://example.test/v1/chat/completions",
        )

    def test_keeps_full_endpoint(self) -> None:
        self.assertEqual(
            config(base_url="https://example.test/v1/chat/completions").endpoint,
            "https://example.test/v1/chat/completions",
        )

    def test_rejects_missing_key(self) -> None:
        with self.assertRaises(ValidationError):
            config(api_key="  ")

    def test_rejects_invalid_base_url(self) -> None:
        with self.assertRaises(ValidationError):
            config(base_url="ftp://example.test")

    def test_rejects_invalid_limits(self) -> None:
        with self.assertRaises(ValidationError):
            config(timeout=0)
        with self.assertRaises(ValidationError):
            config(max_calls=-1)

    def test_defaults_point_to_opencode_v2(self) -> None:
        self.assertIn("/inference/go/openai/v1", DEFAULT_BASE_URL)
        self.assertEqual(DEFAULT_MODEL, "deepseek-v4.1-flash")


class OpenCodeChatClientTests(unittest.TestCase):
    def client(self, opener: FakeOpener, **overrides: object) -> OpenCodeChatClient:
        return OpenCodeChatClient(
            config(**overrides),
            logger=null_logger("ai"),
            opener=opener,
            sleep=lambda _seconds: None,
        )

    def test_sends_expected_request(self) -> None:
        opener = FakeOpener(completion("feat: añade panel"))
        client = self.client(opener)

        result = client.complete("sistema", "usuario")

        self.assertEqual(result, "feat: añade panel")
        request, timeout = opener.requests[0]
        assert hasattr(request, "full_url")
        self.assertEqual(request.full_url, "https://example.test/openai/v1/chat/completions")
        self.assertEqual(timeout, 30.0)
        self.assertEqual(request.get_header("Authorization"), "Bearer oc_sk_test")
        self.assertEqual(request.get_header("User-agent"), f"autocommit/{__version__}")
        self.assertTrue(request.get_header("X-opencode-session").startswith("ses_"))
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "deepseek-v4.1-flash")
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertEqual(body["messages"][1]["content"], "usuario")

    def test_supports_content_parts(self) -> None:
        opener = FakeOpener(
            completion([{"type": "text", "text": "feat: "}, {"text": "añade panel"}])
        )
        self.assertEqual(self.client(opener).complete("s", "u"), "feat: añade panel")

    def test_does_not_retry_auth_errors(self) -> None:
        opener = FakeOpener(http_error(401, '{"error":"invalid key"}'))
        with self.assertRaises(AIError) as context:
            self.client(opener).complete("s", "u")
        self.assertIn("401", str(context.exception))
        self.assertIn("invalid key", str(context.exception))
        self.assertEqual(len(opener.requests), 1)

    def test_retries_server_errors(self) -> None:
        opener = FakeOpener(http_error(503), completion("fix: corrige el bug"))
        self.assertEqual(self.client(opener).complete("s", "u"), "fix: corrige el bug")
        self.assertEqual(len(opener.requests), 2)

    def test_gives_up_after_retries(self) -> None:
        opener = FakeOpener(http_error(500), http_error(500))
        with self.assertRaises(AIError):
            self.client(opener).complete("s", "u")
        self.assertEqual(len(opener.requests), 2)

    def test_retries_network_errors(self) -> None:
        opener = FakeOpener(urllib.error.URLError("sin red"), completion("docs: actualiza la guía"))
        self.assertEqual(self.client(opener).complete("s", "u"), "docs: actualiza la guía")

    def test_invalid_json_raises(self) -> None:
        class InvalidResponse(FakeResponse):
            def read(self) -> bytes:
                return b"no es json"

        class Opener:
            def __call__(self, request: object, timeout: float | None = None):
                return InvalidResponse({})

        with self.assertRaises(AIError):
            OpenCodeChatClient(config(), logger=null_logger("ai"), opener=Opener()).complete(
                "s", "u"
            )

    def test_empty_choices_raises(self) -> None:
        opener = FakeOpener({"choices": []})
        with self.assertRaises(AIError):
            self.client(opener).complete("s", "u")


class StubFallback:
    def __init__(self) -> None:
        self.used = 0

    def create(self, files: list[str]) -> str:
        self.used += 1
        return "chore: heurística"


class StubClient:
    def __init__(self, result: str | Exception) -> None:
        self.result = result
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class AIMessageFactoryTests(unittest.TestCase):
    def test_uses_ai_message_when_valid(self) -> None:
        client = StubClient("feat(ui): añade panel de depósitos")
        fallback = StubFallback()
        factory = AIMessageFactory(client, fallback, logger=null_logger("ai"))

        self.assertEqual(factory.create(["src/panel.tsx"]), "feat(ui): añade panel de depósitos")
        self.assertEqual(factory.calls, 1)
        self.assertEqual(factory.failures, 0)
        self.assertEqual(fallback.used, 0)

    def test_falls_back_when_response_is_invalid(self) -> None:
        client = StubClient("he añadido un panel")
        fallback = StubFallback()
        factory = AIMessageFactory(client, fallback, logger=null_logger("ai"))

        self.assertEqual(factory.create(["src/panel.tsx"]), "chore: heurística")
        self.assertEqual(factory.failures, 1)
        self.assertEqual(fallback.used, 1)

    def test_falls_back_when_client_raises(self) -> None:
        client = StubClient(AIError("sin conexión"))
        fallback = StubFallback()
        factory = AIMessageFactory(client, fallback, logger=null_logger("ai"))

        self.assertEqual(factory.create(["a.py"]), "chore: heurística")
        self.assertEqual(factory.failures, 1)

    def test_respects_max_calls(self) -> None:
        client = StubClient("feat: añade x")
        fallback = StubFallback()
        factory = AIMessageFactory(client, fallback, max_calls=1, logger=null_logger("ai"))

        self.assertEqual(factory.create(["a.py"]), "feat: añade x")
        self.assertEqual(factory.create(["b.py"]), "chore: heurística")
        self.assertEqual(client.calls, 1)

    def test_rejects_empty_file_list(self) -> None:
        with self.assertRaises(ValueError):
            AIMessageFactory(StubClient("feat: x"), StubFallback()).create([])

    def test_passes_context_to_client(self) -> None:
        class CapturingClient:
            def __init__(self) -> None:
                self.prompts: list[str] = []

            def complete(self, system: str, user: str) -> str:
                self.prompts.append(user)
                return "feat: mensaje"

        client = CapturingClient()
        factory = AIMessageFactory(
            client,
            StubFallback(),
            logger=null_logger("ai"),
            context_provider=lambda files: "--- a.py ---\ncontenido real",
        )
        self.assertEqual(factory.create(["a.py"]), "feat: mensaje")
        self.assertIn("Extracto del contenido", client.prompts[0])
        self.assertIn("contenido real", client.prompts[0])


class ResolveApiKeyTests(TemporaryProjectTestCase):
    def key_file(self) -> Path:
        return self.tmp / "opencode_key"

    def test_explicit_key_wins(self) -> None:
        write_secret_file(self.key_file(), "oc_sk_file")
        with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_env"}, clear=True):
            value = resolve_api_key(
                explicit="oc_sk_explicit",
                key_file=self.key_file(),
                logger=null_logger("ai"),
            )
        self.assertEqual(value, "oc_sk_explicit")

    def test_environment_wins_over_file(self) -> None:
        write_secret_file(self.key_file(), "oc_sk_file")
        with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_env"}, clear=True):
            value = resolve_api_key(key_file=self.key_file(), logger=null_logger("ai"))
        self.assertEqual(value, "oc_sk_env")

    def test_reads_key_file(self) -> None:
        write_secret_file(self.key_file(), "oc_sk_file")
        with mock.patch.dict(os.environ, {}, clear=True):
            value = resolve_api_key(key_file=self.key_file(), logger=null_logger("ai"))
        self.assertEqual(value, "oc_sk_file")

    def test_prompts_and_saves_key(self) -> None:
        prompter = ScriptedPrompter(secrets=["oc_sk_prompt"], confirmations=[True])
        with mock.patch.dict(os.environ, {}, clear=True):
            value = resolve_api_key(
                key_file=self.key_file(),
                prompter=prompter,
                logger=null_logger("ai"),
            )
        self.assertEqual(value, "oc_sk_prompt")
        self.assertEqual(read_secret_file(self.key_file()), "oc_sk_prompt")

    def test_prompts_without_saving(self) -> None:
        prompter = ScriptedPrompter(secrets=["oc_sk_prompt"], confirmations=[False])
        with mock.patch.dict(os.environ, {}, clear=True):
            value = resolve_api_key(
                key_file=self.key_file(),
                prompter=prompter,
                logger=null_logger("ai"),
            )
        self.assertEqual(value, "oc_sk_prompt")
        self.assertIsNone(read_secret_file(self.key_file()))

    def test_rejects_empty_prompt(self) -> None:
        prompter = ScriptedPrompter(secrets=["   "])
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            self.assertRaises(ValidationError),
        ):
            resolve_api_key(
                key_file=self.key_file(),
                prompter=prompter,
                logger=null_logger("ai"),
            )

    def test_requires_a_key_without_prompter(self) -> None:
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            self.assertRaises(ValidationError) as context,
        ):
            resolve_api_key(key_file=self.key_file(), logger=null_logger("ai"))
        self.assertIn("OPENCODE_API_KEY", str(context.exception))
        self.assertIn("--ai-api-key-file", str(context.exception))


class CliWiringTests(TemporaryProjectTestCase):
    def parse(self, *extra: str) -> object:
        return build_parser().parse_args(
            ["--ai", "--ai-api-key-file", str(self.tmp / "missing_key"), *extra]
        )

    def test_ai_is_disabled_by_default(self) -> None:
        args = build_parser().parse_args([])
        self.assertIsNone(build_message_factory(args, Settings(), null_logger("cli")))

    def test_ai_requires_api_key(self) -> None:
        args = self.parse()
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            self.assertRaises(ValidationError),
        ):
            build_message_factory(args, Settings(), null_logger("cli"))

    def test_ai_builds_factory_with_environment_key(self) -> None:
        args = self.parse()
        with mock.patch.dict(os.environ, {"OPENCODE_API_KEY": "oc_sk_test"}, clear=True):
            factory = build_message_factory(args, Settings(seed=1), null_logger("cli"))
        self.assertIsInstance(factory, AIMessageFactory)

    def test_ai_builds_factory_with_explicit_key(self) -> None:
        args = self.parse("--ai-api-key", "oc_sk_explicit")
        with mock.patch.dict(os.environ, {}, clear=True):
            factory = build_message_factory(args, Settings(seed=1), null_logger("cli"))
        self.assertIsInstance(factory, AIMessageFactory)

    def test_ai_builds_factory_with_key_file(self) -> None:
        key_file = self.tmp / "opencode_key"
        write_secret_file(key_file, "oc_sk_file")
        args = build_parser().parse_args(["--ai", "--ai-api-key-file", str(key_file)])
        with mock.patch.dict(os.environ, {}, clear=True):
            factory = build_message_factory(args, Settings(seed=1), null_logger("cli"))
        self.assertIsInstance(factory, AIMessageFactory)

    def test_custom_key_variable(self) -> None:
        args = self.parse("--ai-api-key-env", "MI_CLAVE")
        with mock.patch.dict(os.environ, {"MI_CLAVE": "oc_sk_test"}, clear=True):
            factory = build_message_factory(args, Settings(seed=1), null_logger("cli"))
        self.assertIsInstance(factory, AIMessageFactory)

    def test_env_file_supplies_key_and_options(self) -> None:
        env_path = self.write(
            "vars.env",
            "OPENCODE_API_KEY=oc_sk_desde_archivo\n"
            "AUTOCOMMIT_AI_MODEL=glm-5.3-flash\n"
            "AUTOCOMMIT_AI_BASE_URL=https://example.test/openai/v1\n",
        )
        args = self.parse("--env-file", str(env_path))
        with mock.patch.dict(os.environ, {}, clear=True):
            applied = load_env_file(env_path)
            config = resolve_ai_config(args, null_logger("cli"))
        self.assertIn("OPENCODE_API_KEY", applied)
        self.assertEqual(config.api_key, "oc_sk_desde_archivo")
        self.assertEqual(config.model, "glm-5.3-flash")
        self.assertEqual(config.base_url, "https://example.test/openai/v1")

    def test_flags_win_over_environment(self) -> None:
        args = self.parse(
            "--ai-api-key",
            "oc_sk_flag",
            "--ai-model",
            "kimi-k2.7-code",
            "--ai-base-url",
            "https://flag.test/v1",
        )
        with mock.patch.dict(
            os.environ,
            {
                "OPENCODE_API_KEY": "oc_sk_env",
                "AUTOCOMMIT_AI_MODEL": "glm-5.3-flash",
                "AUTOCOMMIT_AI_BASE_URL": "https://env.test/v1",
            },
            clear=True,
        ):
            config = resolve_ai_config(args, null_logger("cli"))
        self.assertEqual(config.api_key, "oc_sk_flag")
        self.assertEqual(config.model, "kimi-k2.7-code")
        self.assertEqual(config.base_url, "https://flag.test/v1")


if __name__ == "__main__":
    unittest.main()

"""Validación de contenido y redacción de credenciales.

Responsabilidades:

* Detectar posibles secretos en los archivos antes de publicarlos.
* Rechazar nombres de archivo sensibles (`.env`, claves privadas, etc.).
* Impedir archivos que superen el tamaño máximo configurado.
* Redactar credenciales en cualquier texto que vaya a un log o a la consola.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

MAX_SCAN_BYTES = 512 * 1024
MAX_FINDINGS_PER_FILE = 5

DOC_SUFFIXES = frozenset({".md", ".rst", ".txt", ".adoc"})

# Plantillas de entorno: no llevan secretos reales y su contenido se escanea
# igualmente. Solo se permiten estos nombres exactos.
ENV_TEMPLATE_NAMES = frozenset({".env.example", ".env.sample", ".env.template", ".env.dist"})

SENSITIVE_NAME_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.keystore",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.*",
    "secrets.*",
)

PLACEHOLDERS = (
    "your",
    "example",
    "sample",
    "changeme",
    "change-me",
    "placeholder",
    "xxxx",
    "***",
    "dummy",
    "fake",
    "redacted",
    "${",
    "{{",
    "...",
    "<",
)


@dataclass(frozen=True)
class SecretPattern:
    name: str
    description: str
    regex: re.Pattern[str]


SECRET_PATTERNS = (
    SecretPattern(
        "github-token",
        "token de GitHub",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    ),
    SecretPattern(
        "github-fine-grained",
        "token de GitHub",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    ),
    SecretPattern(
        "aws-access-key",
        "clave de acceso de AWS",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ),
    SecretPattern(
        "google-api-key",
        "clave de API de Google",
        re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ),
    SecretPattern(
        "slack-token",
        "token de Slack",
        re.compile(r"\bxox[abposr]-[0-9A-Za-z-]{10,}"),
    ),
    SecretPattern(
        "stripe-key",
        "clave de Stripe",
        re.compile(r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{16,}\b"),
    ),
    SecretPattern(
        "private-key",
        "clave privada",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
    ),
    SecretPattern(
        "jwt",
        "JSON Web Token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
)

_GENERIC_SECRET_RE = re.compile(
    r"""(?ix)
    \b(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?token|auth[_-]?token
        |client[_-]?secret|password|passwd)\b
    \s*[:=]\s*
    ['"]([^'"\s]{8,})['"]
    """
)

_URL_WITH_CREDENTIALS_RE = re.compile(r"\b(https?|ssh|git)://[^/\s@]+@")
_TOKEN_LIKE_RE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")


def redact(text: str, replacement: str = "***") -> str:
    """Elimina credenciales de un texto antes de mostrarlo o registrarlo."""
    if not text:
        return text
    redacted = _URL_WITH_CREDENTIALS_RE.sub(
        lambda match: f"{match.group(1)}://{replacement}@", text
    )
    return _TOKEN_LIKE_RE.sub(replacement, redacted)


class RedactingFilter(logging.Filter):
    """Filtro de logging que redacta credenciales en el mensaje final."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensivo ante args mal formados
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


@dataclass(frozen=True)
class SecretFinding:
    path: str
    pattern: str
    description: str
    line: int


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDERS)


class SecretScanner:
    """Busca patrones conocidos de secretos en archivos de texto."""

    def __init__(
        self,
        patterns: Sequence[SecretPattern] = SECRET_PATTERNS,
        *,
        max_bytes: int = MAX_SCAN_BYTES,
        scan_generic: bool = True,
    ) -> None:
        self._patterns = tuple(patterns)
        self._max_bytes = max_bytes
        self._scan_generic = scan_generic

    def scan(self, path: Path, relative_path: str | None = None) -> list[SecretFinding]:
        display = relative_path or str(path)
        try:
            with path.open("rb") as handle:
                raw = handle.read(self._max_bytes)
        except OSError:
            return []

        if b"\x00" in raw[:2048]:
            return []

        text = raw.decode("utf-8", errors="ignore")
        findings: list[SecretFinding] = []
        seen: set[tuple[str, int]] = set()

        for pattern in self._patterns:
            for match in pattern.regex.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                key = (pattern.name, line)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(SecretFinding(display, pattern.name, pattern.description, line))
                if len(findings) >= MAX_FINDINGS_PER_FILE:
                    return findings

        if self._scan_generic and path.suffix.lower() not in DOC_SUFFIXES:
            for match in _GENERIC_SECRET_RE.finditer(text):
                if _looks_like_placeholder(match.group(1)):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                key = ("generic-secret", line)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    SecretFinding(
                        display,
                        "generic-secret",
                        "valor con apariencia de credencial",
                        line,
                    )
                )
                if len(findings) >= MAX_FINDINGS_PER_FILE:
                    break

        return findings


@dataclass(frozen=True)
class Rejection:
    path: str
    reason: str


@dataclass(frozen=True)
class ValidationReport:
    accepted: tuple[str, ...]
    rejected: tuple[Rejection, ...]

    @property
    def rejected_paths(self) -> tuple[str, ...]:
        return tuple(rejection.path for rejection in self.rejected)


def _matches_sensitive_name(name: str) -> bool:
    if name.lower() in ENV_TEMPLATE_NAMES:
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in SENSITIVE_NAME_PATTERNS)


class FileValidator:
    """Decide que archivos pueden publicarse y explica por que se rechazan."""

    def __init__(
        self,
        root: Path,
        max_size_bytes: int,
        *,
        allow_secrets: bool = False,
        scanner: SecretScanner | None = None,
    ) -> None:
        self._root = Path(root)
        self._max_size_bytes = max_size_bytes
        self._allow_secrets = allow_secrets
        self._scanner = scanner or SecretScanner()

    def validate(self, paths: Sequence[str]) -> ValidationReport:
        accepted: list[str] = []
        rejected: list[Rejection] = []

        for relative in paths:
            reason = self._rejection_reason(relative)
            if reason is None:
                accepted.append(relative)
            else:
                rejected.append(Rejection(relative, reason))

        return ValidationReport(tuple(accepted), tuple(rejected))

    def _rejection_reason(self, relative: str) -> str | None:
        path = self._root / relative
        try:
            size = path.stat().st_size
        except OSError:
            return "no se pudo leer el archivo"

        if size > self._max_size_bytes:
            limit = self._max_size_bytes / (1024 * 1024)
            return f"supera el tamaño máximo permitido ({limit:.1f} MB)"

        if self._allow_secrets:
            return None

        if _matches_sensitive_name(path.name):
            return "el nombre sugiere un archivo sensible (.env, claves, credenciales)"

        findings = self._scanner.scan(path, relative)
        if findings:
            details = ", ".join(
                f"{finding.description} en la línea {finding.line}" for finding in findings[:3]
            )
            return f"posible secreto detectado: {details}"

        return None

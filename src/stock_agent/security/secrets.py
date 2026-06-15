"""Secret loading policy for local runtime code.

Secrets may be read only from environment variables or an explicitly supplied
local secret mapping. Telegram, LLM, and remote-style request sources are never
allowed to ask this module for secret values.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

from stock_agent.security.redaction import REDACTED

_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_BLOCKED_SOURCES = {"telegram", "llm"}


class SecretNotFound(RuntimeError):
    """Raised when a configured secret reference cannot be resolved."""


class SecretAccessBlocked(PermissionError):
    """Raised when an unsafe source attempts to read a secret."""


@dataclass(frozen=True)
class SecretValue:
    name: str
    value: str
    source: str

    @property
    def redacted(self) -> str:
        return REDACTED

    def as_header(self, header_name: str = "Authorization", scheme: str = "Bearer") -> dict[str, str]:
        return {header_name: f"{scheme} {self.value}"}

    def describe(self) -> dict[str, str]:
        return {"name": self.name, "source": self.source, "value": self.redacted}


def load_secret_from_env(
    env_name: str,
    *,
    environ: Mapping[str, str] | None = None,
    source: str = "local_runtime",
) -> SecretValue:
    _ensure_source_allowed(source)
    if not _ENV_NAME.fullmatch(env_name):
        raise SecretNotFound(f"invalid secret env name {env_name!r}")
    resolved_environ = environ if environ is not None else os.environ
    value = resolved_environ.get(env_name)
    if not value:
        raise SecretNotFound(f"missing secret env {env_name}")
    return SecretValue(name=env_name, value=value, source="env")


def load_secret(
    reference: str,
    *,
    environ: Mapping[str, str] | None = None,
    local_secrets: Mapping[str, str] | None = None,
    source: str = "local_runtime",
) -> SecretValue:
    """Resolve ``env:NAME`` or ``local:NAME`` secret references.

    Direct literal secrets are deliberately rejected, so normal config files can
    hold only references instead of credential material.
    """

    _ensure_source_allowed(source)
    if reference.startswith("env:"):
        return load_secret_from_env(reference.removeprefix("env:"), environ=environ, source=source)
    if reference.startswith("local:"):
        name = reference.removeprefix("local:")
        value = (local_secrets or {}).get(name)
        if not value:
            raise SecretNotFound(f"missing local secret {name}")
        return SecretValue(name=name, value=value, source="local")
    raise SecretAccessBlocked("secret references must use env:NAME or local:NAME")


def _ensure_source_allowed(source: str) -> None:
    if source in _BLOCKED_SOURCES:
        raise SecretAccessBlocked(f"{source} is not allowed to read secrets")


__all__ = [
    "SecretAccessBlocked",
    "SecretNotFound",
    "SecretValue",
    "load_secret",
    "load_secret_from_env",
]

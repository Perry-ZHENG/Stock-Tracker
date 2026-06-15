"""Sensitive-data redaction helpers.

These helpers are intentionally conservative at persistence and notification
boundaries. Business logic may receive secrets in memory, but logs, trace,
SQLite payloads, and lake records should only see redacted forms.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "auth_token",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "private_key",
    "account_id",
    "account_number",
    "account_ref",
    "accountref",
)

_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|authorization|credential)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(account[_-]?(?:id|number|ref))\s*[:=]\s*([a-z0-9._:-]+)"),
)


def redact_sensitive(value: Any, *, extra_secrets: list[str] | tuple[str, ...] = ()) -> Any:
    """Return a recursively redacted copy of ``value``.

    Dict keys that look sensitive are replaced entirely. Strings also have
    common inline secret patterns and any caller-provided secret literals
    replaced with ``[REDACTED]``.
    """

    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_sensitive(item, extra_secrets=extra_secrets)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item, extra_secrets=extra_secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item, extra_secrets=extra_secrets) for item in value)
    if isinstance(value, str):
        return redact_text(value, extra_secrets=extra_secrets)
    return value


def redact_text(text: str | None, *, extra_secrets: list[str] | tuple[str, ...] = ()) -> str | None:
    if text is None:
        return None
    redacted = text
    for secret in extra_secrets:
        if secret:
            redacted = redacted.replace(secret, REDACTED)
    for pattern in _SENSITIVE_TEXT_PATTERNS:
        redacted = pattern.sub(_replace_sensitive_match, redacted)
    return redacted


def _replace_sensitive_match(match: re.Match[str]) -> str:
    if match.lastindex and match.lastindex >= 2:
        return f"{match.group(1)}={REDACTED}"
    return REDACTED


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    if "redacted" in normalized:
        return False
    if normalized.endswith("_env") or normalized in {"env", "environment"}:
        return False
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


__all__ = ["REDACTED", "redact_sensitive", "redact_text"]

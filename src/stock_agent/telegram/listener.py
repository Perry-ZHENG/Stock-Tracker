"""Telegram listener skeleton.

This module intentionally does not import a Telegram SDK yet. It provides the
authorization and command-handling core that a real bot listener can call later.
"""

from __future__ import annotations

import copy
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from stock_agent.commands.query_cli import run_cli_query
from stock_agent.config import DEFAULT_CONFIG
from stock_agent.config_changes import create_config_change

TelegramRole = Literal["user", "admin"]


@dataclass(frozen=True)
class TelegramCommandResult:
    ok: bool
    message: str
    role: TelegramRole | None = None
    change_id: str | None = None


def resolve_telegram_role(
    *,
    user_id: int,
    allowed_user_ids: list[int],
    admin_user_ids: list[int] | None = None,
) -> TelegramRole | None:
    admins = set(admin_user_ids or [])
    if user_id in admins:
        return "admin"
    if user_id in set(allowed_user_ids):
        return "user"
    return None


def handle_telegram_message(
    *,
    root: Path,
    connection: sqlite3.Connection,
    user_id: int,
    text: str,
    allowed_user_ids: list[int],
    admin_user_ids: list[int] | None = None,
) -> TelegramCommandResult:
    role = resolve_telegram_role(
        user_id=user_id,
        allowed_user_ids=allowed_user_ids,
        admin_user_ids=admin_user_ids,
    )
    if role is None:
        return TelegramCommandResult(ok=False, role=None, message="telegram_error=user is not allowed")

    parts = text.strip().split()
    if not parts:
        return TelegramCommandResult(ok=False, role=role, message="telegram_error=empty command")

    command = parts[0].lower()
    if command in {"/signals", "signals"}:
        return _query(root, "signals", role=role, limit=_limit(parts))
    if command in {"/health", "health"}:
        return _query(root, "health", role=role, limit=_limit(parts))
    if command in {"/news", "news"}:
        return _query(root, "news", role=role, limit=_limit(parts), symbol=_symbol(parts))
    if command in {"/config", "config"}:
        return _handle_config_request(connection, role=role, parts=parts)

    return TelegramCommandResult(
        ok=False,
        role=role,
        message="telegram_error=unsupported command; supported: /signals, /health, /news, /config add-symbol SYMBOL",
    )


def _query(
    root: Path,
    query: str,
    *,
    role: TelegramRole,
    limit: int,
    symbol: str | None = None,
) -> TelegramCommandResult:
    from io import StringIO

    stream = StringIO()
    exit_code = run_cli_query(root, query=query, limit=limit, symbol=symbol, stream=stream)  # type: ignore[arg-type]
    return TelegramCommandResult(
        ok=exit_code == 0,
        role=role,
        message=stream.getvalue().strip(),
    )


def _handle_config_request(
    connection: sqlite3.Connection,
    *,
    role: TelegramRole,
    parts: list[str],
) -> TelegramCommandResult:
    if role != "admin":
        return TelegramCommandResult(
            ok=False,
            role=role,
            message="telegram_error=config changes require admin role and CLI approval",
        )
    if len(parts) != 3 or parts[1].lower() != "add-symbol":
        return TelegramCommandResult(
            ok=False,
            role=role,
            message="telegram_error=usage: /config add-symbol SYMBOL",
        )

    symbol = parts[2].upper()
    before_config = copy.deepcopy(DEFAULT_CONFIG)
    after_config = copy.deepcopy(DEFAULT_CONFIG)
    if symbol not in after_config["symbols"]["default"]:
        after_config["symbols"]["default"] = [*after_config["symbols"]["default"], symbol]
    diff = f"symbols.default +{symbol}"
    now = datetime.now(UTC)
    change_id = _change_id(symbol, now)
    change = create_config_change(
        connection,
        change_id=change_id,
        source="telegram",
        before_config=before_config,
        after_config=after_config,
        diff=diff,
        status="pending_review",
        now=now,
    )
    return TelegramCommandResult(
        ok=True,
        role=role,
        change_id=change_id,
        message=(
            f"config_change={change['change_id']} status={change['status']} "
            "requires CLI review before apply"
        ),
    )


def _limit(parts: list[str]) -> int:
    if len(parts) < 2:
        return 5
    try:
        return max(1, int(parts[1]))
    except ValueError:
        return 5


def _symbol(parts: list[str]) -> str | None:
    if len(parts) < 2:
        return None
    if parts[1].isdigit():
        return None
    return parts[1].upper()


def _change_id(symbol: str, timestamp: datetime) -> str:
    digest = hashlib.sha1(f"telegram|{symbol}|{timestamp.isoformat()}".encode("utf-8")).hexdigest()[:12]
    return f"chg-telegram-{digest}"

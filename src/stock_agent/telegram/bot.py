"""Telegram bot adapter core with optional SDK integration."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from stock_agent.config_changes import create_config_change
from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.dialog.intents import ClarificationIntent, HighRiskBlockedIntent, PendingChangeIntent, ReadOnlyIntent
from stock_agent.dialog.llm_parser import LlmParser
from stock_agent.query import QueryService
from stock_agent.telegram.listener import TelegramCommandResult, handle_telegram_message, resolve_telegram_role


@dataclass(frozen=True)
class TelegramBotSettings:
    token: str | None
    allowed_user_ids: list[int]
    admin_user_ids: list[int]
    allowed_chat_ids: list[int]


@dataclass(frozen=True)
class TelegramUpdate:
    user_id: int
    chat_id: int
    text: str


@dataclass(frozen=True)
class TelegramOutboundMessage:
    ok: bool
    chat_id: int
    text: str
    role: str | None = None
    change_id: str | None = None


@dataclass(frozen=True)
class TelegramBotStartup:
    ok: bool
    status: str
    reason: str | None = None
    sdk_available: bool = False


class TelegramBot:
    def __init__(
        self,
        *,
        root: Path,
        connection: sqlite3.Connection,
        settings: TelegramBotSettings,
        config_context: RuntimeConfigContext | None = None,
        llm_parser: LlmParser | None = None,
    ) -> None:
        self.root = root
        self.connection = connection
        self.settings = settings
        self.config_context = config_context or load_config(root)
        self.llm_parser = llm_parser or LlmParser(enabled=False)

    def handle_update(self, update: TelegramUpdate) -> TelegramOutboundMessage:
        role = resolve_telegram_role(
            user_id=update.user_id,
            allowed_user_ids=self.settings.allowed_user_ids,
            admin_user_ids=self.settings.admin_user_ids,
        )
        if role is None:
            return TelegramOutboundMessage(ok=False, chat_id=update.chat_id, role=None, text="telegram_error=user is not allowed")
        if self.settings.allowed_chat_ids and update.chat_id not in set(self.settings.allowed_chat_ids):
            return TelegramOutboundMessage(ok=False, chat_id=update.chat_id, role=role, text="telegram_error=chat is not allowed")

        if update.text.strip().startswith("/") or update.text.strip().split(" ", 1)[0].lower() in {
            "signals",
            "health",
            "news",
            "schedule",
            "trace",
            "config",
        }:
            result = handle_telegram_message(
                root=self.root,
                connection=self.connection,
                user_id=update.user_id,
                text=update.text,
                allowed_user_ids=self.settings.allowed_user_ids,
                admin_user_ids=self.settings.admin_user_ids,
                config_context=self.config_context,
            )
            return _outbound(update.chat_id, result)

        intent = self.llm_parser.parse(update.text)
        if isinstance(intent, ReadOnlyIntent):
            result = QueryService(self.root, config_context=self.config_context).execute(
                intent.query,
                limit=intent.limit,
                symbol=intent.symbol,
                period=intent.period or "day",
                target_id=intent.target_id,
                from_value=intent.from_ts,
                to_value=intent.to_ts,
                output_format="telegram",
            )
            return TelegramOutboundMessage(ok=result.ok, chat_id=update.chat_id, role=role, text=result.text)
        if isinstance(intent, PendingChangeIntent):
            if role != "admin":
                return TelegramOutboundMessage(
                    ok=False,
                    chat_id=update.chat_id,
                    role=role,
                    text="telegram_error=config changes require admin role and CLI approval",
                )
            change_id = _record_pending_change(self.connection, intent, config_context=self.config_context)
            return TelegramOutboundMessage(
                ok=True,
                chat_id=update.chat_id,
                role=role,
                change_id=change_id,
                text=f"config_change={change_id} status=pending_review requires CLI review before apply",
            )
        if isinstance(intent, HighRiskBlockedIntent):
            return TelegramOutboundMessage(ok=False, chat_id=update.chat_id, role=role, text=intent.safety_message)
        if isinstance(intent, ClarificationIntent):
            return TelegramOutboundMessage(ok=False, chat_id=update.chat_id, role=role, text=f"{intent.question}\nexamples: {', '.join(intent.candidates)}")
        return TelegramOutboundMessage(ok=False, chat_id=update.chat_id, role=role, text="telegram_error=unsupported intent")


def check_telegram_bot_startup(settings: TelegramBotSettings) -> TelegramBotStartup:
    if not settings.token:
        return TelegramBotStartup(ok=False, status="disabled", reason="missing telegram token")
    return TelegramBotStartup(
        ok=True,
        status="ready",
        sdk_available=importlib.util.find_spec("telegram") is not None,
    )


def _record_pending_change(
    connection: sqlite3.Connection,
    intent: PendingChangeIntent,
    *,
    config_context: RuntimeConfigContext,
) -> str:
    before_config = copy.deepcopy(config_context.raw_config)
    after_config = _apply_pending_change(copy.deepcopy(config_context.raw_config), intent)
    now = datetime.now(UTC)
    change_id = _change_id(intent, now)
    create_config_change(
        connection,
        change_id=change_id,
        source="telegram",
        before_config=before_config,
        after_config=after_config,
        diff=_diff_text(intent),
        status="pending_review",
        now=now,
    )
    return change_id


def _apply_pending_change(config: dict, intent: PendingChangeIntent) -> dict:
    if intent.action == "add_symbol" and intent.symbol:
        symbols = list(config["symbols"]["default"])
        if intent.symbol not in symbols:
            config["symbols"]["default"] = [*symbols, intent.symbol]
    elif intent.action == "remove_symbol" and intent.symbol:
        config["symbols"]["default"] = [symbol for symbol in config["symbols"]["default"] if symbol != intent.symbol]
    elif intent.action in {"enable_strategy", "disable_strategy"} and intent.strategy_id:
        if intent.strategy_id in config["strategies"]:
            config["strategies"][intent.strategy_id]["enabled"] = intent.action == "enable_strategy"
    elif intent.action == "change_watch_window" and intent.watch_window:
        config["schedule"].update(intent.watch_window)
    return config


def _outbound(chat_id: int, result: TelegramCommandResult) -> TelegramOutboundMessage:
    return TelegramOutboundMessage(
        ok=result.ok,
        chat_id=chat_id,
        text=result.message,
        role=result.role,
        change_id=result.change_id,
    )


def _diff_text(intent: PendingChangeIntent) -> str:
    if intent.symbol:
        return f"{intent.action} {intent.symbol}"
    if intent.strategy_id:
        return f"{intent.action} {intent.strategy_id}"
    if intent.watch_window:
        return f"{intent.action} {intent.watch_window}"
    return intent.action


def _change_id(intent: PendingChangeIntent, timestamp: datetime) -> str:
    payload = f"telegram|{intent.action}|{intent.symbol}|{intent.strategy_id}|{timestamp.isoformat()}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"chg-telegram-{digest}"


__all__ = [
    "TelegramBot",
    "TelegramBotSettings",
    "TelegramBotStartup",
    "TelegramOutboundMessage",
    "TelegramUpdate",
    "check_telegram_bot_startup",
]

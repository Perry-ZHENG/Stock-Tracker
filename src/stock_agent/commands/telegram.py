"""Telegram command entrypoint skeleton."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from threading import Event
from typing import TextIO

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.services.entrypoints import ResearchEntryAdapter
from stock_agent.services.production_v2 import ProductionV2Components, build_production_v2
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.telegram.bot import (
    TelegramBot,
    TelegramBotSettings,
    TelegramUpdate,
)
from stock_agent.telegram.transport import TelegramHttpApi, TelegramTransportError


def run_telegram(
    root: Path,
    *,
    stream: TextIO | None = None,
    config_context: RuntimeConfigContext | None = None,
    api: TelegramHttpApi | None = None,
    stop_event: Event | None = None,
    once: bool = False,
    research_entry: ResearchEntryAdapter | None = None,
) -> int:
    output = stream or sys.stdout
    config_context = config_context or load_config(root)
    config = config_context.config
    token = os.getenv(config.telegram.token_env)

    if not config.telegram.enabled:
        output.write("telegram_status=disabled\nreason=telegram.enabled is false\n")
        output.flush()
        return 0
    if not token:
        output.write(
            f"telegram_status=disabled\nreason=missing token env {config.telegram.token_env}\n"
        )
        output.flush()
        return 0

    output.write("telegram_status=ready\n")
    output.write("listener=long_polling\n")
    output.write(f"workspace={root}\n")
    output.flush()
    owned_v2_components: ProductionV2Components | None = None
    if research_entry is None:
        owned_v2_components = build_production_v2(root, config_context=config_context)
        research_entry = ResearchEntryAdapter(owned_v2_components.service)
    connection = initialize_runtime_database(root, config)
    bot = TelegramBot(
        root=root,
        connection=connection,
        settings=TelegramBotSettings(
            token=token,
            allowed_user_ids=config.telegram.allowed_user_ids,
            admin_user_ids=config.telegram.admin_user_ids,
            allowed_chat_ids=config.telegram.allowed_chat_ids,
        ),
        config_context=config_context,
        research_entry=research_entry,
    )
    transport = api or TelegramHttpApi(token)
    stop = stop_event or Event()
    offset = 0
    try:
        while not stop.is_set():
            bot.input_gate.heartbeat("telegram", actor_ref="telegram_bot")
            try:
                updates = transport.get_updates(offset=offset, timeout_sec=20)
                for raw_update in updates:
                    update_id = raw_update.get("update_id")
                    if isinstance(update_id, int):
                        offset = max(offset, update_id + 1)
                    parsed = _parse_update(raw_update)
                    if parsed is None:
                        continue
                    response = bot.handle_update(parsed)
                    transport.send_message(chat_id=response.chat_id, text=response.text)
            except TelegramTransportError as exc:
                output.write(f"telegram_transport_error={exc}\n")
                output.flush()
                if once:
                    return 1
                if stop.wait(2):
                    break
            if once:
                break
    except KeyboardInterrupt:
        output.write("telegram_status=stopped\nreason=keyboard_interrupt\n")
        output.flush()
    finally:
        bot.input_gate.mark_offline("telegram")
        connection.close()
        if owned_v2_components is not None:
            owned_v2_components.close()
    return 0


def _parse_update(raw_update: dict) -> TelegramUpdate | None:
    message = raw_update.get("message")
    if not isinstance(message, dict):
        return None
    sender = message.get("from")
    chat = message.get("chat")
    text = message.get("text")
    if not isinstance(sender, dict) or not isinstance(chat, dict) or not isinstance(text, str):
        return None
    user_id = sender.get("id")
    chat_id = chat.get("id")
    if not isinstance(user_id, int) or not isinstance(chat_id, int):
        return None
    return TelegramUpdate(user_id=user_id, chat_id=chat_id, text=text)


__all__ = ["run_telegram"]

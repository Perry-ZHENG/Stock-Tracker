"""Interactive CLI shell."""

from __future__ import annotations

import copy
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from stock_agent.config_changes import create_config_change
from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.dialog.intents import (
    ClarificationIntent,
    HighRiskBlockedIntent,
    LocalAdminIntent,
    PendingChangeIntent,
    ReadOnlyIntent,
)
from stock_agent.dialog.parser import parse_structured_command
from stock_agent.query import QueryService
from stock_agent.security.trading_firewall import TradingActionFirewall, blocked_message
from stock_agent.storage.sqlite import initialize_runtime_database


def run_interactive_cli(
    root: Path,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    config_context: RuntimeConfigContext | None = None,
) -> int:
    input_stream = input_stream or sys.stdin
    output = output_stream or sys.stdout
    config_context = config_context or load_config(root)
    output.write("stock-agent interactive cli\n")
    output.write("type 'exit' or 'quit' to leave\n")

    while True:
        output.write("stock-agent> ")
        output.flush()
        line = input_stream.readline()
        if line == "":
            output.write("\n")
            output.flush()
            return 0
        text = line.strip()
        if text.lower() in {"exit", "quit"}:
            output.write("bye\n")
            output.flush()
            return 0
        if not text:
            continue

        intent = parse_structured_command(text, source="cli")
        if isinstance(intent, ReadOnlyIntent):
            output.write(_execute_read_only(root, intent, config_context=config_context))
        elif isinstance(intent, PendingChangeIntent):
            output.write(_handle_pending_change(root, intent, input_stream=input_stream, config_context=config_context))
        elif isinstance(intent, HighRiskBlockedIntent):
            output.write(_handle_blocked_intent(root, intent, config_context=config_context))
        elif isinstance(intent, ClarificationIntent):
            output.write(f"clarification_required=true\nquestion={intent.question}\n")
            output.write("examples:\n")
            for example in intent.candidates:
                output.write(f"- {example}\n")
        elif isinstance(intent, LocalAdminIntent):
            output.write(f"local_admin_intent={intent.action} is not wired in interactive mode yet\n")
        output.flush()


def _execute_read_only(root: Path, intent: ReadOnlyIntent, *, config_context: RuntimeConfigContext) -> str:
    result = QueryService(root, config_context=config_context).execute(
        intent.query,
        limit=intent.limit,
        symbol=intent.symbol,
        period=intent.period or "day",
        target_id=intent.target_id,
        from_value=intent.from_ts,
        to_value=intent.to_ts,
    )
    return result.text


def _handle_blocked_intent(
    root: Path,
    intent: HighRiskBlockedIntent,
    *,
    config_context: RuntimeConfigContext,
) -> str:
    connection = initialize_runtime_database(root, config_context.config)
    try:
        decision = TradingActionFirewall(connection).inspect_intent(
            intent,
            source="cli",
            actor_ref="local_cli",
        )
    finally:
        connection.close()
    return blocked_message(intent.requested_action, audit_id=decision.audit_id)


def _handle_pending_change(
    root: Path,
    intent: PendingChangeIntent,
    *,
    input_stream: TextIO,
    config_context: RuntimeConfigContext,
) -> str:
    before_config = copy.deepcopy(config_context.raw_config)
    after_config = _apply_pending_change(copy.deepcopy(config_context.raw_config), intent)
    if after_config == before_config:
        return f"pending_change_status=noop action={intent.action}\n"

    prompt = f"pending_change action={intent.action} requires confirmation; type yes to record: "
    confirmation = input_stream.readline().strip().lower()
    if confirmation != "yes":
        return prompt + "\npending_change_status=cancelled\n"

    connection = initialize_runtime_database(root, config_context.config)
    try:
        now = datetime.now(UTC)
        change_id = _change_id(intent, now)
        create_config_change(
            connection,
            change_id=change_id,
            source="cli",
            before_config=before_config,
            after_config=after_config,
            diff=_diff_text(intent),
            status="pending_review",
            now=now,
        )
    finally:
        connection.close()
    return prompt + f"\nconfig_change={change_id} status=pending_review requires CLI approve\n"


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


def _diff_text(intent: PendingChangeIntent) -> str:
    if intent.symbol:
        return f"{intent.action} {intent.symbol}"
    if intent.strategy_id:
        return f"{intent.action} {intent.strategy_id}"
    if intent.watch_window:
        return f"{intent.action} {intent.watch_window}"
    return intent.action


def _change_id(intent: PendingChangeIntent, timestamp: datetime) -> str:
    payload = f"cli|{intent.action}|{intent.symbol}|{intent.strategy_id}|{timestamp.isoformat()}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"chg-cli-{digest}"


__all__ = ["run_interactive_cli"]

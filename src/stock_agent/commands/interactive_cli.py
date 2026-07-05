"""Interactive CLI shell."""

from __future__ import annotations

import copy
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
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
from stock_agent.dialog.interaction import ChatClient, build_interaction_plan, format_interaction_plan
from stock_agent.dialog.input_gate import InputGate, InputGateError
from stock_agent.dialog.langchain_adapter import build_langchain_client
from stock_agent.dialog.llm_parser import LlmParser
from stock_agent.query import QueryService
from stock_agent.security.trading_firewall import TradingActionFirewall, blocked_message
from stock_agent.storage.sqlite import initialize_runtime_database


def run_interactive_cli(
    root: Path,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    config_context: RuntimeConfigContext | None = None,
    llm_parser: LlmParser | None = None,
    chat_client: ChatClient | None = None,
) -> int:
    input_stream = input_stream or sys.stdin
    output = output_stream or sys.stdout
    config_context = config_context or load_config(root)
    langchain_client = build_langchain_client(config_context.config.llm)
    llm_parser = llm_parser or LlmParser(enabled=langchain_client is not None, client=langchain_client)
    chat_client = chat_client or langchain_client
    output.write("stock-agent interactive cli\n")
    output.write("type 'exit' or 'quit' to leave\n")
    gate_connection = initialize_runtime_database(root, config_context.config)
    gate = InputGate.from_config(gate_connection, config_context.config.input_control)
    actor_ref = "local_cli"
    shown_switch_requests: set[str] = set()
    heartbeat_stop = Event()
    heartbeat_thread = Thread(
        target=_run_cli_heartbeat,
        args=(root, config_context, actor_ref, heartbeat_stop),
        name="stock-agent-cli-input-heartbeat",
        daemon=True,
    )

    try:
        startup_decision = gate.check("cli", actor_ref=actor_ref)
        output.write(
            f"input_status={'active' if startup_decision.allowed else 'standby'}\n"
            f"message={startup_decision.message}\n"
        )
        output.flush()
        heartbeat_thread.start()
        while True:
            for request in gate.pending_for("cli"):
                if request.request_id not in shown_switch_requests:
                    output.write(
                        "input_switch_approval_required=true\n"
                        f"request_id={request.request_id}\n"
                        f"from={request.from_source}\n"
                        f"to={request.to_source}\n"
                        f"expires_at={request.as_dict()['expires_at']}\n"
                        f"approve with: approve {request.request_id}\n"
                        f"reject with: reject {request.request_id}\n"
                    )
                    shown_switch_requests.add(request.request_id)

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
                gate.heartbeat("cli", actor_ref=actor_ref)
                continue

            if _handle_input_control_command(
                gate,
                text,
                actor_ref=actor_ref,
                output=output,
            ):
                output.flush()
                continue

            decision = gate.check("cli", actor_ref=actor_ref)
            if not decision.allowed:
                output.write(f"input_status=blocked\nmessage={decision.message}\n")
                output.write("type yes to request input switch: ")
                output.flush()
                confirmation = input_stream.readline().strip().lower()
                if confirmation == "yes":
                    try:
                        request = gate.request_switch("cli", actor_ref=actor_ref)
                    except InputGateError as exc:
                        output.write(f"\ninput_switch_status=failed\nmessage={exc}\n")
                    else:
                        output.write(
                            "\ninput_switch_status=pending\n"
                            f"request_id={request.request_id}\n"
                            f"approval_required_from={request.from_source}\n"
                        )
                else:
                    output.write("\ninput_switch_status=cancelled\n")
                output.flush()
                continue

            plan = build_interaction_plan(text, llm_parser=llm_parser, chat_client=chat_client)
            if plan.is_chat:
                output.write(format_interaction_plan(plan))
                output.flush()
                continue
            if plan.requires_confirmation:
                output.write(format_interaction_plan(plan))
                output.write("execute? type yes to continue: ")
                output.flush()
                confirmation = input_stream.readline().strip().lower()
                if confirmation != "yes":
                    output.write("\nexecution_status=cancelled\n")
                    output.flush()
                    continue
                output.write("\n")

            intent = plan.intent
            if isinstance(intent, ReadOnlyIntent):
                output.write(_execute_read_only(root, intent, config_context=config_context))
            elif isinstance(intent, PendingChangeIntent):
                output.write(
                    _handle_pending_change(
                        root,
                        intent,
                        input_stream=input_stream,
                        config_context=config_context,
                        already_confirmed=plan.requires_confirmation,
                    )
                )
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
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)
        gate.mark_offline("cli", actor_ref=actor_ref)
        gate_connection.close()


def _handle_input_control_command(
    gate: InputGate,
    text: str,
    *,
    actor_ref: str,
    output: TextIO,
) -> bool:
    parts = text.split()
    if not parts or parts[0].lower() not in {"approve", "reject"}:
        return False
    if len(parts) != 2:
        output.write("input_switch_status=failed\nmessage=usage: approve|reject REQUEST_ID\n")
        return True
    try:
        request = gate.decide(
            parts[1],
            source="cli",
            actor_ref=actor_ref,
            approve=parts[0].lower() == "approve",
        )
    except InputGateError as exc:
        output.write(f"input_switch_status=failed\nmessage={exc}\n")
    else:
        output.write(
            f"input_switch_status={request.status}\n"
            f"request_id={request.request_id}\n"
            f"active_input={request.to_source if request.status == 'approved' else request.from_source}\n"
        )
    return True


def _run_cli_heartbeat(
    root: Path,
    config_context: RuntimeConfigContext,
    actor_ref: str,
    stop: Event,
) -> None:
    while not stop.wait(15):
        connection = initialize_runtime_database(root, config_context.config)
        try:
            InputGate.from_config(
                connection,
                config_context.config.input_control,
            ).heartbeat("cli", actor_ref=actor_ref)
        finally:
            connection.close()


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
    already_confirmed: bool = False,
) -> str:
    before_config = copy.deepcopy(config_context.raw_config)
    after_config = _apply_pending_change(copy.deepcopy(config_context.raw_config), intent)
    if after_config == before_config:
        return f"pending_change_status=noop action={intent.action}\n"

    prompt = f"pending_change action={intent.action} requires confirmation; type yes to record: "
    if not already_confirmed:
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
    if already_confirmed:
        return f"config_change={change_id} status=pending_review requires CLI approve\n"
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

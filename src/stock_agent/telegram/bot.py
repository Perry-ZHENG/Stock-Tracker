"""Telegram bot adapter core with optional SDK integration."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from stock_agent.config_changes import create_config_change
from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.contracts.tasks import ResearchRequest
from stock_agent.dialog.intents import ClarificationIntent, HighRiskBlockedIntent, PendingChangeIntent, ReadOnlyIntent
from stock_agent.dialog.input_gate import InputGate, InputGateError
from stock_agent.dialog.llm_parser import LlmParser
from stock_agent.query import QueryService
from stock_agent.security.trading_firewall import TradingActionFirewall
from stock_agent.services.entrypoints import ResearchEntryAdapter, ResearchEntryError
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
        research_entry: ResearchEntryAdapter | None = None,
    ) -> None:
        self.root = root
        self.connection = connection
        self.settings = settings
        self.config_context = config_context or load_config(root)
        self.llm_parser = llm_parser or LlmParser(enabled=False)
        self.research_entry = research_entry
        self.input_gate = InputGate.from_config(
            connection,
            self.config_context.config.input_control,
        )
        self.input_gate.heartbeat("telegram", actor_ref="telegram_bot")

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

        actor_ref = f"user:{update.user_id}:chat:{update.chat_id}"
        control_result = self._handle_input_control(update, role=role, actor_ref=actor_ref)
        if control_result is not None:
            return control_result

        preparsed_intent = None
        if not update.text.strip().startswith("/"):
            preparsed_intent = self.llm_parser.parse(update.text)
            if isinstance(preparsed_intent, HighRiskBlockedIntent):
                decision = TradingActionFirewall(self.connection).inspect_intent(
                    preparsed_intent,
                    source="telegram",
                    actor_ref=actor_ref,
                )
                return TelegramOutboundMessage(
                    ok=False,
                    chat_id=update.chat_id,
                    role=role,
                    text=decision.message,
                )

        decision = self.input_gate.check("telegram", actor_ref=actor_ref)
        if not decision.allowed:
            return TelegramOutboundMessage(
                ok=False,
                chat_id=update.chat_id,
                role=role,
                text=(
                    f"input_status=blocked\n{decision.message}\n"
                    "发送 /input request 申请切换至 Telegram。"
                ),
            )

        research_result = self._handle_research_command(update, role=role, actor_ref=actor_ref)
        if research_result is not None:
            return research_result

        if update.text.strip().startswith("/") or update.text.strip().split(" ", 1)[0].lower() in {
            "signals",
            "health",
            "news",
            "schedule",
            "provider-compare",
            "abnormal-bars",
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

        intent = preparsed_intent or self.llm_parser.parse(update.text)
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
            decision = TradingActionFirewall(self.connection).inspect_intent(
                intent,
                source="telegram",
                actor_ref=f"user:{update.user_id}:chat:{update.chat_id}",
            )
            return TelegramOutboundMessage(ok=False, chat_id=update.chat_id, role=role, text=decision.message)
        if isinstance(intent, ClarificationIntent):
            return TelegramOutboundMessage(ok=False, chat_id=update.chat_id, role=role, text=f"{intent.question}\nexamples: {', '.join(intent.candidates)}")
        return TelegramOutboundMessage(ok=False, chat_id=update.chat_id, role=role, text="telegram_error=unsupported intent")

    def pending_approval_messages(self, *, chat_id: int) -> list[TelegramOutboundMessage]:
        """Return approval prompts for delivery by the Telegram transport loop."""
        messages: list[TelegramOutboundMessage] = []
        for request in self.input_gate.pending_for("telegram"):
            messages.append(
                TelegramOutboundMessage(
                    ok=True,
                    chat_id=chat_id,
                    role=None,
                    text=(
                        "input_switch_approval_required=true\n"
                        f"request_id={request.request_id}\n"
                        f"{request.to_source} 正在申请成为输入接口。\n"
                        f"批准：/input approve {request.request_id}\n"
                        f"拒绝：/input reject {request.request_id}"
                    ),
                )
            )
        return messages

    def _handle_input_control(
        self,
        update: TelegramUpdate,
        *,
        role: str,
        actor_ref: str,
    ) -> TelegramOutboundMessage | None:
        parts = update.text.strip().split()
        if not parts or parts[0].lower() not in {"/input", "input"}:
            return None
        if len(parts) < 2:
            return TelegramOutboundMessage(
                ok=False,
                chat_id=update.chat_id,
                role=role,
                text="usage: /input status|request|approve REQUEST_ID|reject REQUEST_ID",
            )
        action = parts[1].lower()
        try:
            if action == "status":
                state = self.input_gate.state()
                return TelegramOutboundMessage(
                    ok=True,
                    chat_id=update.chat_id,
                    role=role,
                    text=(
                        f"active_input={state.active_source or 'none'}\n"
                        f"active_online={str(state.active_online).lower()}\n"
                        f"pending_requests={len(state.pending_requests)}"
                    ),
                )
            if action == "request":
                request = self.input_gate.request_switch("telegram", actor_ref=actor_ref)
                return TelegramOutboundMessage(
                    ok=True,
                    chat_id=update.chat_id,
                    role=role,
                    text=(
                        "input_switch_status=pending\n"
                        f"request_id={request.request_id}\n"
                        f"approval_required_from={request.from_source}"
                    ),
                )
            if action in {"approve", "reject"} and len(parts) == 3:
                request = self.input_gate.decide(
                    parts[2],
                    source="telegram",
                    actor_ref=actor_ref,
                    approve=action == "approve",
                )
                return TelegramOutboundMessage(
                    ok=True,
                    chat_id=update.chat_id,
                    role=role,
                    text=(
                        f"input_switch_status={request.status}\n"
                        f"request_id={request.request_id}"
                    ),
                )
        except InputGateError as exc:
            return TelegramOutboundMessage(
                ok=False,
                chat_id=update.chat_id,
                role=role,
                text=f"input_switch_status=failed\nmessage={exc}",
            )
        return TelegramOutboundMessage(
            ok=False,
            chat_id=update.chat_id,
            role=role,
            text="usage: /input status|request|approve REQUEST_ID|reject REQUEST_ID",
        )

    def _handle_research_command(
        self,
        update: TelegramUpdate,
        *,
        role: str,
        actor_ref: str,
    ) -> TelegramOutboundMessage | None:
        text = update.text.strip()
        command, _, remainder = text.partition(" ")
        if command.lower() not in {"/research", "research"}:
            return None
        if self.research_entry is None:
            return TelegramOutboundMessage(
                ok=False,
                chat_id=update.chat_id,
                role=role,
                text="research_status=unavailable\nmessage=V2 AgentService is not configured",
            )

        action, _, argument = remainder.strip().partition(" ")
        action = action.lower()
        try:
            if action == "submit":
                request = ResearchRequest.model_validate_json(argument)
                status = self.research_entry.submit(
                    request,
                    source="telegram",
                    actor_ref=actor_ref,
                    actor_type="human_admin" if role == "admin" else "human_user",
                )
                return _research_outbound(update.chat_id, role, status, action="submitted")
            if action == "status" and argument:
                status = self.research_entry.status(
                    argument.strip(),
                    source="telegram",
                    actor_ref=actor_ref,
                    actor_type="human_admin" if role == "admin" else "human_user",
                )
                return _research_outbound(update.chat_id, role, status, action="status")
            if action in {"pause", "resume", "cancel"} and argument:
                status = self.research_entry.control(
                    argument.strip(),
                    action,
                    source="telegram",
                    actor_ref=actor_ref,
                    actor_type="human_admin" if role == "admin" else "human_user",
                )
                return _research_outbound(update.chat_id, role, status, action=action)
            if action == "input":
                task_id, step_id, payload = _parse_research_input(argument)
                status = self.research_entry.provide_input(
                    task_id,
                    step_id,
                    payload,
                    source="telegram",
                    actor_ref=actor_ref,
                    actor_type="human_admin" if role == "admin" else "human_user",
                )
                return _research_outbound(update.chat_id, role, status, action="input_received")
            if action == "report" and argument:
                task_id, _, report_id = argument.partition(" ")
                report = self.research_entry.report(
                    task_id,
                    report_id.strip() or None,
                    source="telegram",
                    actor_ref=actor_ref,
                    actor_type="human_admin" if role == "admin" else "human_user",
                )
                return TelegramOutboundMessage(
                    ok=True,
                    chat_id=update.chat_id,
                    role=role,
                    text=(
                        f"research_task={task_id}\n"
                        f"report_id={report['report_id']}\n"
                        f"summary={report['draft']['summary']}"
                    ),
                )
        except (ResearchEntryError, ValueError, json.JSONDecodeError) as exc:
            return TelegramOutboundMessage(
                ok=False,
                chat_id=update.chat_id,
                role=role,
                text=f"research_status=failed\nmessage={exc}",
            )
        return TelegramOutboundMessage(
            ok=False,
            chat_id=update.chat_id,
            role=role,
            text=(
                "usage: /research submit REQUEST_JSON | status TASK_ID | "
                "pause TASK_ID | resume TASK_ID | cancel TASK_ID | "
                "input TASK_ID STEP_ID PAYLOAD_JSON | report TASK_ID [REPORT_ID]"
            ),
        )


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


def _research_outbound(
    chat_id: int,
    role: str,
    status: dict[str, object],
    *,
    action: str,
) -> TelegramOutboundMessage:
    task = status["task"]
    assert isinstance(task, dict)
    return TelegramOutboundMessage(
        ok=True,
        chat_id=chat_id,
        role=role,
        text=(
            f"research_action={action}\n"
            f"task_id={task['task_id']}\n"
            f"status={task['status']}\n"
            f"report_id={status.get('report_id') or 'pending'}"
        ),
    )


def _parse_research_input(argument: str) -> tuple[str, str, dict[str, object]]:
    task_id, separator, remainder = argument.strip().partition(" ")
    step_id, separator_two, payload_text = remainder.strip().partition(" ")
    if not task_id or not separator or not step_id or not separator_two:
        raise ValueError("input requires TASK_ID STEP_ID PAYLOAD_JSON")
    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise ValueError("research input payload must be a JSON object")
    return task_id, step_id, payload


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

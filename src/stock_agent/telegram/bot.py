"""Optional Telegram transport for V2 research task operations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.contracts.reports import FinalReport
from stock_agent.contracts.tasks import ResearchRequest
from stock_agent.dialog.input_gate import InputGate, InputGateError
from stock_agent.reports.renderers import render_report
from stock_agent.services.entrypoints import ResearchEntryAdapter, ResearchEntryError


@dataclass(frozen=True)
class TelegramBotSettings:
    token: str
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
    role: str
    text: str


class TelegramBot:
    """Translate constrained chat commands to the shared V2 entry adapter."""

    def __init__(
        self,
        *,
        root: Path,
        connection: sqlite3.Connection,
        settings: TelegramBotSettings,
        config_context: RuntimeConfigContext | None = None,
        research_entry: ResearchEntryAdapter | None = None,
    ) -> None:
        self.root = root
        self.connection = connection
        self.settings = settings
        self.config_context = config_context or load_config(root)
        self.research_entry = research_entry
        self.input_gate = InputGate.from_config(connection, self.config_context.config.input_control)

    def handle_update(self, update: TelegramUpdate) -> TelegramOutboundMessage:
        role = self._role(update)
        if role is None:
            return TelegramOutboundMessage(False, update.chat_id, "unauthorized", "access denied")
        actor_ref = f"user:{update.user_id}:chat:{update.chat_id}"
        self.input_gate.heartbeat("telegram", actor_ref=actor_ref)
        command, _, remainder = update.text.strip().partition(" ")
        if command.lower() == "/input":
            return self._handle_input(update, role=role, actor_ref=actor_ref, argument=remainder)
        if command.lower() in {"/research", "research"}:
            return self._handle_research(update, role=role, actor_ref=actor_ref, argument=remainder)
        return TelegramOutboundMessage(
            True,
            update.chat_id,
            role,
            "usage: /research submit REQUEST_JSON | status TASK_ID | report TASK_ID | pause|resume|cancel TASK_ID",
        )

    def _handle_input(
        self,
        update: TelegramUpdate,
        *,
        role: str,
        actor_ref: str,
        argument: str,
    ) -> TelegramOutboundMessage:
        action, _, request_id = argument.strip().partition(" ")
        try:
            if action == "status":
                return TelegramOutboundMessage(True, update.chat_id, role, json.dumps(self.input_gate.state().as_dict(), ensure_ascii=False))
            if action == "request":
                request = self.input_gate.request_switch("telegram", actor_ref=actor_ref)
                return TelegramOutboundMessage(True, update.chat_id, role, f"input_switch_status=pending\nrequest_id={request.request_id}")
            if action in {"approve", "reject"} and request_id and role == "admin":
                request = self.input_gate.decide(
                    request_id,
                    source="telegram",
                    actor_ref=actor_ref,
                    approve=action == "approve",
                )
                return TelegramOutboundMessage(True, update.chat_id, role, f"input_switch_status={request.status}\nrequest_id={request.request_id}")
        except InputGateError as exc:
            return TelegramOutboundMessage(False, update.chat_id, role, f"input_switch_status=failed\nmessage={exc}")
        return TelegramOutboundMessage(False, update.chat_id, role, "usage: /input status|request|approve REQUEST_ID|reject REQUEST_ID")

    def _handle_research(
        self,
        update: TelegramUpdate,
        *,
        role: str,
        actor_ref: str,
        argument: str,
    ) -> TelegramOutboundMessage:
        if self.research_entry is None:
            return TelegramOutboundMessage(False, update.chat_id, role, "research_status=unavailable")
        action, _, payload = argument.strip().partition(" ")
        try:
            if action == "submit":
                self._require_input(actor_ref)
                status = self.research_entry.submit(
                    ResearchRequest.model_validate_json(payload),
                    source="telegram",
                    actor_ref=actor_ref,
                    actor_type="human_admin" if role == "admin" else "human_user",
                )
                return _status_message(update.chat_id, role, status, "submitted")
            if action == "status" and payload:
                return _status_message(
                    update.chat_id,
                    role,
                    self.research_entry.status(payload.strip(), source="telegram", actor_ref=actor_ref),
                    "status",
                )
            if action == "report" and payload:
                report = self.research_entry.report(payload.strip(), None, source="telegram", actor_ref=actor_ref)
                markdown = render_report(FinalReport.model_validate(report), "markdown").decode("utf-8")
                return TelegramOutboundMessage(True, update.chat_id, role, markdown[:3900])
            if action in {"pause", "resume", "cancel", "retry-report"} and payload:
                self._require_input(actor_ref)
                status = self.research_entry.control(payload.strip(), action, source="telegram", actor_ref=actor_ref)
                return _status_message(update.chat_id, role, status, action)
        except (InputGateError, ResearchEntryError, ValueError) as exc:
            return TelegramOutboundMessage(False, update.chat_id, role, f"research_status=failed\nmessage={exc}")
        return TelegramOutboundMessage(False, update.chat_id, role, "invalid research command")

    def _role(self, update: TelegramUpdate) -> str | None:
        if self.settings.allowed_user_ids and update.user_id not in self.settings.allowed_user_ids:
            return None
        if self.settings.allowed_chat_ids and update.chat_id not in self.settings.allowed_chat_ids:
            return None
        return "admin" if update.user_id in self.settings.admin_user_ids else "user"

    def _require_input(self, actor_ref: str) -> None:
        decision = self.input_gate.check("telegram", actor_ref=actor_ref)
        if not decision.allowed:
            raise InputGateError(decision.message)


def _status_message(chat_id: int, role: str, status: dict[str, object], action: str) -> TelegramOutboundMessage:
    task = status["task"]
    assert isinstance(task, dict)
    return TelegramOutboundMessage(
        True,
        chat_id,
        role,
        "\n".join(
            [
                f"research_action={action}",
                f"task_id={task['task_id']}",
                f"status={task['status']}",
                f"report_id={status.get('report_id') or 'pending'}",
            ]
        ),
    )


__all__ = ["TelegramBot", "TelegramBotSettings", "TelegramOutboundMessage", "TelegramUpdate"]

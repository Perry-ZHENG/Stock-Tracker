"""Audited web-agent planning and execution."""

from __future__ import annotations

import copy
import hashlib
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from stock_agent.agent.runner import ReactToolAgent
from stock_agent.agent.runtime import build_model_agent
from stock_agent.config_changes import create_config_change
from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.dialog.intents import (
    ClarificationIntent,
    HighRiskBlockedIntent,
    LocalAdminIntent,
    PendingChangeIntent,
    ReadOnlyIntent,
    validate_intent,
)
from stock_agent.dialog.interaction import build_interaction_plan
from stock_agent.dialog.input_gate import InputGate, InputGateError
from stock_agent.dialog.langchain_adapter import build_langchain_client
from stock_agent.dialog.llm_parser import LlmParser
from stock_agent.query import QueryService
from stock_agent.security.trading_firewall import TradingActionFirewall, blocked_message
from stock_agent.storage.repositories import get_agent_run, insert_agent_run
from stock_agent.storage.sqlite import initialize_runtime_database


class WebAgentError(ValueError):
    pass


class WebAgentService:
    def __init__(
        self,
        root: Path,
        *,
        config_context: RuntimeConfigContext | None = None,
        llm_parser: LlmParser | None = None,
        react_agent: ReactToolAgent | None = None,
    ) -> None:
        self.root = root
        self.config_context = config_context or load_config(root)
        langchain_client = build_langchain_client(self.config_context.config.llm)
        self.llm_parser = llm_parser or LlmParser(
            enabled=langchain_client is not None,
            client=langchain_client,
        )
        self.react_agent = react_agent or build_model_agent(
            root,
            config_context=self.config_context,
        )

    def plan(self, text: str, *, actor_ref: str = "local_web") -> dict[str, object]:
        raw_text = text.strip()
        if not raw_text:
            raise WebAgentError("message must not be empty")
        decision = self._check_input(actor_ref)
        if not decision["allowed"]:
            return {
                "status": "input_blocked",
                "output": decision["message"],
                "active_input": decision["active_source"],
                "requested_input": "fastapi",
                "can_request_switch": bool(decision["active_online"]),
                "requires_confirmation": False,
            }
        if self.react_agent is not None:
            return self._plan_with_react(raw_text, actor_ref=actor_ref)
        started = time.perf_counter()
        created_at = datetime.now(UTC)
        run_id = f"run-web-{uuid4().hex[:12]}"
        plan = build_interaction_plan(raw_text, llm_parser=self.llm_parser)
        intent = plan.intent
        status = "planned"
        output: str | None = plan.chat_response
        trace_id: str | None = None
        requires_confirmation = isinstance(intent, PendingChangeIntent)

        if plan.is_chat:
            status = "succeeded"
        elif isinstance(intent, ReadOnlyIntent):
            result = QueryService(self.root, config_context=self.config_context).execute(
                intent.query,
                limit=intent.limit,
                symbol=intent.symbol,
                period=intent.period or "day",
                target_id=intent.target_id,
                from_value=intent.from_ts,
                to_value=intent.to_ts,
            )
            status = "succeeded" if result.ok else "failed"
            output = result.text
            trace_id = intent.target_id if intent.query == "trace" else None
        elif isinstance(intent, HighRiskBlockedIntent):
            connection = initialize_runtime_database(self.root, self.config_context.config)
            try:
                decision = TradingActionFirewall(connection).inspect_intent(
                    intent,
                    source="web",
                    actor_ref="local_web",
                )
            finally:
                connection.close()
            status = "blocked"
            output = blocked_message(intent.requested_action, audit_id=decision.audit_id)
        elif isinstance(intent, ClarificationIntent):
            status = "clarification"
            output = f"{intent.question}\nexamples: {', '.join(intent.candidates)}"
        elif isinstance(intent, LocalAdminIntent):
            status = "blocked"
            output = "Local administration remains CLI-only."

        duration_ms = (time.perf_counter() - started) * 1000
        payload = intent.model_dump(mode="json") if intent is not None else None
        connection = initialize_runtime_database(self.root, self.config_context.config)
        try:
            insert_agent_run(
                connection,
                run_id=run_id,
                source="web",
                raw_text=raw_text,
                parser_name=plan.parser_name,
                intent=payload,
                risk=getattr(intent, "risk", None),
                status=status,
                command_preview=plan.command_preview,
                output=output,
                trace_id=trace_id,
                duration_ms=duration_ms,
                created_at=created_at,
                updated_at=datetime.now(UTC),
            )
            run = get_agent_run(connection, run_id)
        finally:
            connection.close()
        assert run is not None
        return {**run, "requires_confirmation": requires_confirmation}

    def _plan_with_react(self, raw_text: str, *, actor_ref: str) -> dict[str, object]:
        started = time.perf_counter()
        created_at = datetime.now(UTC)
        run_id = f"run-agent-{uuid4().hex[:12]}"
        result = self.react_agent.run(raw_text)
        stored_status = {
            "succeeded": "succeeded",
            "needs_user_input": "clarification",
            "no_suitable_tool": "no_suitable_tool",
            "failed": "failed",
        }[result.status]
        intent_payload = {
            "agent_type": "react_tool_agent",
            "selected_tool": result.selected_tool,
            "tool_calls": [call.as_dict() for call in result.tool_calls],
            "actor_ref": actor_ref,
        }
        connection = initialize_runtime_database(self.root, self.config_context.config)
        try:
            insert_agent_run(
                connection,
                run_id=run_id,
                source="web",
                raw_text=raw_text,
                parser_name="react_tool_agent",
                intent=intent_payload,
                risk="read_only",
                status=stored_status,
                command_preview=result.selected_tool,
                output=result.output,
                trace_id=None,
                duration_ms=(time.perf_counter() - started) * 1000,
                created_at=created_at,
                updated_at=datetime.now(UTC),
            )
            run = get_agent_run(connection, run_id)
        finally:
            connection.close()
        assert run is not None
        return {
            **run,
            **result.as_dict(),
            "requires_confirmation": False,
        }

    def confirm(self, run_id: str, *, actor_ref: str = "local_web") -> dict[str, object]:
        decision = self._check_input(actor_ref)
        if not decision["allowed"]:
            raise WebAgentError(str(decision["message"]))
        started = time.perf_counter()
        connection = initialize_runtime_database(self.root, self.config_context.config)
        try:
            run = get_agent_run(connection, run_id)
            if run is None:
                raise WebAgentError(f"agent run not found: {run_id}")
            if run["status"] != "planned":
                raise WebAgentError(f"agent run cannot be confirmed from status {run['status']}")
            intent_payload = run["intent"]
            if not isinstance(intent_payload, dict):
                raise WebAgentError("agent run has no executable intent")
            intent = validate_intent(intent_payload)
            if not isinstance(intent, PendingChangeIntent):
                raise WebAgentError("only pending configuration changes require confirmation")

            before_config = copy.deepcopy(self.config_context.raw_config)
            after_config = _apply_pending_change(copy.deepcopy(before_config), intent)
            if after_config == before_config:
                output = f"pending_change_status=noop action={intent.action}"
            else:
                now = datetime.now(UTC)
                change_id = _change_id(intent, now)
                create_config_change(
                    connection,
                    change_id=change_id,
                    source="web",
                    before_config=before_config,
                    after_config=after_config,
                    diff=_diff_text(intent),
                    status="pending_review",
                    now=now,
                )
                output = (
                    f"config_change={change_id} status=pending_review "
                    "requires CLI review before apply"
                )

            insert_agent_run(
                connection,
                run_id=run_id,
                source="web",
                raw_text=str(run["raw_text"]),
                parser_name=str(run["parser_name"]),
                intent=intent_payload,
                risk=str(run["risk"]),
                status="succeeded",
                command_preview=run["command_preview"],
                output=output,
                trace_id=run["trace_id"],
                duration_ms=(time.perf_counter() - started) * 1000,
                created_at=datetime.fromisoformat(str(run["created_at"]).replace("Z", "+00:00")),
                updated_at=datetime.now(UTC),
            )
            updated = get_agent_run(connection, run_id)
        finally:
            connection.close()
        assert updated is not None
        return {**updated, "requires_confirmation": False}

    def input_state(self) -> dict[str, object]:
        connection = initialize_runtime_database(self.root, self.config_context.config)
        try:
            return self._input_gate(connection).state().as_dict()
        finally:
            connection.close()

    def heartbeat(self, *, actor_ref: str) -> dict[str, object]:
        connection = initialize_runtime_database(self.root, self.config_context.config)
        try:
            gate = self._input_gate(connection)
            gate.heartbeat("fastapi", actor_ref=actor_ref)
            return gate.state().as_dict()
        finally:
            connection.close()

    def request_input_switch(self, *, actor_ref: str) -> dict[str, object]:
        connection = initialize_runtime_database(self.root, self.config_context.config)
        try:
            try:
                return self._input_gate(connection).request_switch(
                    "fastapi",
                    actor_ref=actor_ref,
                ).as_dict()
            except InputGateError as exc:
                raise WebAgentError(str(exc)) from exc
        finally:
            connection.close()

    def decide_input_switch(
        self,
        request_id: str,
        *,
        actor_ref: str,
        approve: bool,
    ) -> dict[str, object]:
        connection = initialize_runtime_database(self.root, self.config_context.config)
        try:
            try:
                return self._input_gate(connection).decide(
                    request_id,
                    source="fastapi",
                    actor_ref=actor_ref,
                    approve=approve,
                ).as_dict()
            except InputGateError as exc:
                raise WebAgentError(str(exc)) from exc
        finally:
            connection.close()

    def _check_input(self, actor_ref: str) -> dict[str, object]:
        connection = initialize_runtime_database(self.root, self.config_context.config)
        try:
            gate = self._input_gate(connection)
            decision = gate.check("fastapi", actor_ref=actor_ref)
            state = gate.state()
            return {
                **decision.as_dict(),
                "active_online": state.active_online,
            }
        finally:
            connection.close()

    def _input_gate(self, connection) -> InputGate:
        return InputGate.from_config(
            connection,
            self.config_context.config.input_control,
        )


def _apply_pending_change(config: dict, intent: PendingChangeIntent) -> dict:
    if intent.action == "add_symbol" and intent.symbol:
        symbols = list(config["symbols"]["default"])
        if intent.symbol not in symbols:
            config["symbols"]["default"] = [*symbols, intent.symbol]
    elif intent.action == "remove_symbol" and intent.symbol:
        config["symbols"]["default"] = [
            symbol for symbol in config["symbols"]["default"] if symbol != intent.symbol
        ]
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
    payload = f"web|{intent.action}|{intent.symbol}|{intent.strategy_id}|{timestamp.isoformat()}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"chg-web-{digest}"


__all__ = ["WebAgentError", "WebAgentService"]

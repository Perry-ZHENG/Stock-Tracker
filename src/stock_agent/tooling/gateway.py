"""Policy-enforced Tool Gateway for local and allowlisted MCP research calls."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime

from pydantic import Field

from stock_agent.artifacts.service import ArtifactService
from stock_agent.contracts.common import StrictSchema
from stock_agent.contracts.tasks import ToolError, ToolRequest, ToolResult
from stock_agent.dialog.input_gate import InputGate
from stock_agent.observability import AgentTrace, AgentTraceRecorder, BudgetExceeded, BudgetLedger
from stock_agent.security.redaction import redact_sensitive, redact_text
from stock_agent.security.research_policy import ResearchSafetyPolicy, SafetyRequest
from stock_agent.storage.repositories import insert_trace_chain
from stock_agent.tooling.base import (
    ToolAdapterError,
    ToolAdapterResponse,
    ToolAdapterTimeout,
    ToolArgumentError,
    ToolBudgetExhausted,
    ToolCallBudget,
    ToolRuntimeContext,
)
from stock_agent.tooling.registry import ToolRegistry
from stock_agent.tracing import create_trace


class ToolExecutionResult(StrictSchema):
    """One typed result plus the budget snapshot after this Gateway decision."""

    result: ToolResult
    budget: ToolCallBudget
    trace_id: str | None = None


class ToolGateway:
    """Validate every Tool call before an adapter can contact code or MCP transport."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        connection: sqlite3.Connection | None = None,
        artifact_service: ArtifactService | None = None,
        max_result_bytes: int = 64 * 1024,
        safety_policy: ResearchSafetyPolicy | None = None,
    ) -> None:
        if max_result_bytes <= 0:
            raise ValueError("max_result_bytes must be positive")
        self.registry = registry
        self.connection = connection
        self.artifact_service = artifact_service
        self.max_result_bytes = max_result_bytes
        self.safety_policy = safety_policy or ResearchSafetyPolicy(connection)
        self.budget_ledger = BudgetLedger(connection) if connection is not None else None
        self.trace_recorder = AgentTraceRecorder(connection) if connection is not None else None

    def execute(
        self,
        request: ToolRequest,
        context: ToolRuntimeContext,
        *,
        now: datetime | None = None,
    ) -> ToolExecutionResult:
        active_now = _utc_now(now)
        if request.task_id != context.execution.task_id:
            return self._complete(request, context, _rejected("task_mismatch", "tool request task does not match context"))
        if request.caller != context.execution.actor:
            return self._complete(request, context, _rejected("actor_mismatch", "tool caller does not match context"))
        if request.deadline_at <= active_now:
            return self._complete(request, context, _failure("timed_out", "deadline_elapsed", "tool deadline has elapsed"))

        adapter = self.registry.get(request.tool_name)
        if adapter is None:
            return self._complete(request, context, _rejected("tool_not_allowed", "tool is not in the allowlist"))
        descriptor = adapter.descriptor
        if descriptor.source == "mcp" and not context.execution.allow_mcp:
            return self._complete(request, context, _rejected("mcp_disabled", "MCP is disabled for this task"))
        if context.execution.actor not in descriptor.allowed_roles:
            return self._complete(request, context, _rejected("role_forbidden", "agent role is not allowed to call this tool"))
        if descriptor.permission == "forbidden":
            return self._complete(request, context, _rejected("tool_forbidden", "tool permission is forbidden"))
        if descriptor.permission == "approval_required":
            return self._complete(
                request,
                context,
                _rejected("approval_required", "tool requires an external human approval workflow"),
            )
        input_decision = self._check_input_gate(context)
        if input_decision is not None:
            return self._complete(request, context, input_decision)

        safety = self.safety_policy.inspect(
            SafetyRequest(
                source=descriptor.source,
                actor_type="agent",
                requested_capability="use_mcp" if descriptor.source == "mcp" else "research",
                tool_name=descriptor.name,
                tool_arguments=request.arguments,
                input_trust="untrusted" if descriptor.source == "mcp" else "trusted",
                untrusted_text=descriptor.description if descriptor.source == "mcp" else None,
            )
        )
        if not safety.allowed:
            return self._complete(
                request,
                context,
                _rejected(safety.reason_code, safety.public_message),
                audit_id=safety.audit_id,
            )

        try:
            arguments = adapter.validate_arguments(request.arguments)
        except ToolArgumentError as exc:
            return self._complete(
                request,
                context,
                _rejected("invalid_arguments", redact_text(str(exc)) or "invalid tool arguments"),
            )
        try:
            next_budget = context.execution.budget.consume()
        except ToolBudgetExhausted:
            return self._complete(request, context, _rejected("budget_exhausted", "tool-call budget is exhausted"))
        # Legacy read-only callers may not have an AgentTask yet.  They retain
        # their local budget; official V2 tasks use the durable task ledger.
        if self.budget_ledger is not None and self.budget_ledger.repository.get_task(request.task_id) is not None:
            try:
                self.budget_ledger.consume(request.task_id, tool_calls=1, now=active_now)
            except BudgetExceeded:
                return self._complete(request, context, _rejected("budget_exhausted", "task tool-call budget is exhausted"))

        call_context = ToolRuntimeContext(
            execution=context.execution.model_copy(update={"budget": next_budget}),
            root=context.root,
            config_context=context.config_context,
            deadline_at=request.deadline_at,
        )
        started = time.monotonic()
        try:
            response = adapter.invoke(call_context, arguments)
        except ToolAdapterTimeout as exc:
            return self._complete(
                request,
                call_context,
                _failure("timed_out", "tool_timeout", redact_text(str(exc)) or "tool timed out"),
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        except ToolAdapterError as exc:
            return self._complete(
                request,
                call_context,
                _failure("failed", "tool_failed", redact_text(str(exc)) or "tool failed"),
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        except Exception as exc:  # pragma: no cover - last-resort adapter isolation
            return self._complete(
                request,
                call_context,
                _failure("failed", "tool_unexpected_error", redact_text(str(exc)) or "tool failed"),
                duration_ms=round((time.monotonic() - started) * 1000),
            )

        if response.untrusted:
            injection = self.safety_policy.inspect(
                SafetyRequest(
                    source="mcp",
                    actor_type="tool",
                    requested_capability="use_mcp",
                    input_trust="untrusted",
                    untrusted_text=_serialize_payload(response.payload),
                    tool_name=descriptor.name,
                )
            )
            if not injection.allowed:
                return self._complete(
                    request,
                    call_context,
                    _rejected(injection.reason_code, injection.public_message),
                    audit_id=injection.audit_id,
                )

        raw_payload = _serialize_payload(response.payload)
        if len(raw_payload.encode("utf-8")) > self.max_result_bytes:
            return self._complete(
                request,
                call_context,
                _failure("failed", "result_too_large", "tool result exceeds the configured size limit"),
            )
        artifact_refs = []
        if self.artifact_service is not None:
            try:
                artifact_refs.append(
                    self.artifact_service.save_json(
                        request.task_id,
                        kind="model_response",
                        payload=response.payload,
                        source=f"{descriptor.source}:{descriptor.name}",
                        created_at=active_now,
                    )
                )
            except Exception as exc:
                return self._complete(
                    request,
                    call_context,
                    _failure(
                        "failed",
                        "artifact_store_failed",
                        redact_text(str(exc)) or "tool result could not be stored",
                    ),
                )
        result = ToolResult(
            call_id=request.call_id,
            status="succeeded",
            summary=_summary(response.summary),
            evidence_refs=response.evidence_refs,
            artifact_refs=artifact_refs,
        )
        return self._complete(
            request,
            call_context,
            result,
            duration_ms=round((time.monotonic() - started) * 1000),
        )

    def _check_input_gate(self, context: ToolRuntimeContext) -> ToolResult | None:
        source = context.execution.entry_source
        if source is None:
            return None
        if self.connection is None:
            return _rejected("input_gate_unavailable", "input control requires a database connection")
        actor_ref = context.execution.entry_actor_ref
        if not actor_ref:
            return _rejected("input_actor_missing", "input control requires an actor reference")
        decision = InputGate.from_config(
            self.connection,
            context.config_context.config.input_control,
        ).check(source, actor_ref=actor_ref)
        if decision.allowed:
            return None
        return _rejected("input_gate_blocked", decision.message)

    def _complete(
        self,
        request: ToolRequest,
        context: ToolRuntimeContext,
        result: ToolResult,
        *,
        audit_id: str | None = None,
        duration_ms: int = 0,
    ) -> ToolExecutionResult:
        # Rejected calls do not reach an adapter, but must still retain the
        # request's stable id for audit and idempotency handling.
        result = result.model_copy(update={"call_id": request.call_id})
        trace_id = self._record_trace(request, context, result, audit_id=audit_id, duration_ms=duration_ms)
        return ToolExecutionResult(result=result, budget=context.execution.budget, trace_id=trace_id)

    def _record_trace(
        self,
        request: ToolRequest,
        context: ToolRuntimeContext,
        result: ToolResult,
        *,
        audit_id: str | None,
        duration_ms: int,
    ) -> str | None:
        if self.connection is None:
            return None
        trace_id = f"tool-{request.call_id}"
        trace = create_trace(
            trace_id=trace_id,
            module="tool_gateway",
            input_ref={
                "task_id": request.task_id,
                "tool_name": request.tool_name,
                "caller": request.caller,
                "argument_keys": sorted(request.arguments),
            },
            output_ref={
                "status": result.status,
                "artifact_ids": [artifact.artifact_id for artifact in result.artifact_refs],
                "evidence_ids": [evidence.evidence_id for evidence in result.evidence_refs],
                "audit_id": audit_id,
            },
            status="success" if result.status == "succeeded" else "failed",
            error_msg=result.error.message if result.error is not None else None,
        )
        insert_trace_chain(self.connection, trace)
        if (
            self.trace_recorder is not None
            and self.budget_ledger is not None
            and self.budget_ledger.repository.get_task(request.task_id) is not None
        ):
            descriptor = self.registry.get(request.tool_name)
            source = descriptor.descriptor.source if descriptor is not None else "local"
            self.trace_recorder.record(
                AgentTrace(
                    trace_id=f"trace-v2-tool-{request.call_id}",
                    task_id=request.task_id,
                    component="mcp" if source == "mcp" else "tool",
                    status="success" if result.status == "succeeded" else "failed",
                    duration_ms=duration_ms,
                    input_ref={
                        "tool_name": request.tool_name,
                        "caller": request.caller,
                        "argument_keys": sorted(request.arguments),
                    },
                    output_ref={
                        "status": result.status,
                        "artifact_ids": [artifact.artifact_id for artifact in result.artifact_refs],
                        "evidence_ids": [evidence.evidence_id for evidence in result.evidence_refs],
                        "audit_id": audit_id,
                    },
                    error_message=result.error.message if result.error is not None else None,
                    created_at=_utc_now(None),
                )
            )
        return trace_id


def _rejected(code: str, message: str) -> ToolResult:
    return ToolResult(
        call_id="pending",
        status="rejected",
        summary="tool request rejected",
        error=ToolError(code=code, message=_summary(message)),
    )


def _failure(status: str, code: str, message: str) -> ToolResult:
    return ToolResult(
        call_id="pending",
        status=status,  # type: ignore[arg-type]
        summary="tool request failed",
        error=ToolError(code=code, message=_summary(message)),
    )


def _summary(value: str) -> str:
    return (redact_text(value) or "tool result")[:4_000]


def _serialize_payload(payload: object) -> str:
    return json.dumps(redact_sensitive(payload), ensure_ascii=False, sort_keys=True, default=str)


def _utc_now(value: datetime | None) -> datetime:
    now = value or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Gateway time must be timezone-aware")
    return now.astimezone(UTC)


__all__ = ["ToolExecutionResult", "ToolGateway"]

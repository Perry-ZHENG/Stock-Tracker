"""Durable, role-isolated execution for the five V2 research Agents."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from stock_agent.agents.registry import AgentRegistration, AgentRegistry
from stock_agent.artifacts.service import ArtifactService
from stock_agent.contracts.evidence import ArtifactRef
from stock_agent.contracts.tasks import AgentMessage, AgentStep, AgentTask
from stock_agent.observability import AgentTrace, AgentTraceRecorder, BudgetExceeded, BudgetLedger
from stock_agent.security.redaction import redact_text
from stock_agent.storage.repositories import insert_trace_chain
from stock_agent.storage.task_repository import RepositoryStateError, TaskRepository
from stock_agent.tracing import create_trace

RuntimeModelClient = Callable[[str], str]


class AgentRuntimeError(RuntimeError):
    """Raised when a step cannot safely execute in its registered role boundary."""


@dataclass(frozen=True)
class RuntimeStepResult:
    step: AgentStep
    status: str
    output_artifact_id: str | None = None
    error: str | None = None


class AgentRuntimeContext:
    """Minimal context exposed to one handler; permissions do not come from model output."""

    def __init__(
        self,
        *,
        task: AgentTask,
        step: AgentStep,
        registration: AgentRegistration,
        model_client: RuntimeModelClient | None,
        plan_id: str | None,
        step_trace_id: str,
        budget_ledger: BudgetLedger,
        trace_recorder: AgentTraceRecorder,
        now: datetime,
    ) -> None:
        self.task = task
        self.step = step
        self.registration = registration
        self._model_client = model_client
        self._plan_id = plan_id
        self._step_trace_id = step_trace_id
        self._budget_ledger = budget_ledger
        self._trace_recorder = trace_recorder
        self._now = now
        self._model_calls = 0

    @property
    def model_calls(self) -> int:
        return self._model_calls

    def require_tool(self, tool_name: str) -> None:
        """Reject a tool before a handler can pass it to the shared Tool Gateway."""

        if tool_name not in self.registration.allowed_tools:
            raise AgentRuntimeError(f"tool is not allowed for {self.step.actor}: {tool_name}")

    def call_model(self, prompt: str, output_schema: type[BaseModel]) -> BaseModel:
        """Parse a typed model response and permit exactly one schema-repair attempt."""

        if self._model_client is None:
            raise AgentRuntimeError("no ModelClient is configured for this Agent runtime")
        if len(prompt) > self.registration.max_context_characters:
            raise AgentRuntimeError("model context exceeds the role limit")
        if self.registration.max_model_calls < 1:
            raise AgentRuntimeError("this role is not allowed to call a model")
        raw = self._invoke_model(prompt)
        try:
            return output_schema.model_validate_json(_extract_json(raw))
        except ValidationError as first_error:
            if self._model_calls >= self.registration.max_model_calls:
                raise AgentRuntimeError("model response did not match the required schema") from first_error
            repair_prompt = (
                "Return only JSON matching this schema. Do not add fields or explanations.\n"
                + json.dumps(output_schema.model_json_schema(), ensure_ascii=False, sort_keys=True)
                + "\nInvalid response:\n"
                + raw[: self.registration.max_context_characters // 2]
            )
            repaired = self._invoke_model(repair_prompt)
            try:
                return output_schema.model_validate_json(_extract_json(repaired))
            except ValidationError as second_error:
                raise AgentRuntimeError("model response did not match the required schema after repair") from second_error

    def _invoke_model(self, prompt: str) -> str:
        if self._model_calls >= self.registration.max_model_calls:
            raise AgentRuntimeError("model-call budget is exhausted for this role")
        prompt_tokens = _estimate_tokens(prompt)
        try:
            self._budget_ledger.consume(
                self.task.task_id,
                model_calls=1,
                input_tokens=prompt_tokens,
                now=self._now,
            )
        except BudgetExceeded as exc:
            raise AgentRuntimeError("model-call budget is exhausted for this task") from exc
        self._model_calls += 1
        started = time.monotonic()
        try:
            response = self._model_client(prompt)  # type: ignore[misc]
        except Exception as exc:  # pragma: no cover - provider boundary
            self._trace_recorder.record(
                AgentTrace(
                    trace_id=f"trace-model-{uuid4().hex}",
                    task_id=self.task.task_id,
                    plan_id=self._plan_id,
                    step_id=self.step.step_id,
                    parent_trace_id=self._step_trace_id,
                    component="model",
                    status="failed",
                    duration_ms=round((time.monotonic() - started) * 1000),
                    input_ref={"role": self.step.actor, "prompt_chars": len(prompt)},
                    output_ref={},
                    error_message=str(exc),
                    created_at=self._now,
                )
            )
            raise AgentRuntimeError("model call failed") from exc
        output_tokens = _estimate_tokens(response)
        self._budget_ledger.consume(
            self.task.task_id,
            output_tokens=output_tokens,
            now=self._now,
        )
        self._trace_recorder.record(
            AgentTrace(
                trace_id=f"trace-model-{uuid4().hex}",
                task_id=self.task.task_id,
                plan_id=self._plan_id,
                step_id=self.step.step_id,
                parent_trace_id=self._step_trace_id,
                component="model",
                status="success",
                duration_ms=round((time.monotonic() - started) * 1000),
                input_ref={"role": self.step.actor, "prompt_chars": len(prompt)},
                output_ref={"response_chars": len(response), "estimated_output_tokens": output_tokens},
                created_at=self._now,
            )
        )
        return response


class AgentRuntime:
    """Claim, execute, persist, and trace typed Agent steps without a shared prompt loop."""

    def __init__(
        self,
        *,
        repository: TaskRepository,
        artifact_service: ArtifactService,
        registry: AgentRegistry,
        model_client: RuntimeModelClient | None = None,
        budget_ledger: BudgetLedger | None = None,
        trace_recorder: AgentTraceRecorder | None = None,
    ) -> None:
        self.repository = repository
        self.artifact_service = artifact_service
        self.registry = registry
        self.model_client = model_client
        self.budget_ledger = budget_ledger or BudgetLedger(repository.connection)
        self.trace_recorder = trace_recorder or AgentTraceRecorder(repository.connection)

    def run_ready(
        self,
        task_id: str,
        *,
        worker_id: str,
        limit: int = 1,
        now: datetime | None = None,
    ) -> list[RuntimeStepResult]:
        active_now = _utc_now(now)
        task = self._task(task_id)
        claimed = self.repository.claim_ready_steps(task_id, worker_id=worker_id, limit=limit, claimed_at=active_now)
        return [self.execute_claimed(task, step, now=active_now) for step in claimed]

    def execute_claimed(self, task: AgentTask, step: AgentStep, *, now: datetime | None = None) -> RuntimeStepResult:
        """Execute one already-claimed step and save only typed output as an Artifact."""

        active_now = _utc_now(now)
        started = time.monotonic()
        if step.status != "running":
            raise AgentRuntimeError("only a claimed running step can execute")
        try:
            if (active_now - task.created_at).total_seconds() > task.budget.max_duration_seconds:
                raise AgentRuntimeError("task duration budget is exhausted")
            registration = self.registry.get(step.actor)
            plan = self.repository.get_latest_plan(task.task_id)
            plan_id = plan.plan_id if plan is not None else None
            step_trace_id = f"trace-step-{step.step_id}-{step.attempt}"
            stored_input = self.repository.get_step_input(task.task_id, step.step_id)
            if stored_input is None and registration.input_schema is None:
                # A handler with no typed input still needs a durable payload row
                # before its output can be linked for restart-safe inspection.
                self.repository.save_step_input(task.task_id, step.step_id, None, updated_at=active_now)
            typed_input = _parse_input(stored_input, registration)
            context = AgentRuntimeContext(
                task=task,
                step=step,
                registration=registration,
                model_client=self.model_client,
                plan_id=plan_id,
                step_trace_id=step_trace_id,
                budget_ledger=self.budget_ledger,
                trace_recorder=self.trace_recorder,
                now=active_now,
            )
            output = registration.handler.run(context, typed_input)
            typed_output = _parse_output(output, registration)
            artifact = self.artifact_service.save_json(
                task.task_id,
                kind="model_response",
                payload=_json_payload(typed_output),
                source=f"agent_runtime:{step.actor}",
                created_at=active_now,
            )
            self.repository.record_step_output(
                task.task_id,
                step.step_id,
                artifact_id=artifact.artifact_id,
                updated_at=active_now,
            )
            completed = self.repository.complete_step(
                step.step_id,
                expected_status="running",
                new_status="succeeded",
                updated_at=active_now,
            )
            self._message(task, step, artifact=artifact, now=active_now)
            self._trace(
                task,
                step,
                status="success",
                output_artifact_id=artifact.artifact_id,
                duration_ms=round((time.monotonic() - started) * 1000),
                now=active_now,
            )
            return RuntimeStepResult(step=completed, status="succeeded", output_artifact_id=artifact.artifact_id)
        except Exception as exc:
            error = _safe_error(exc)
            try:
                failed = self.repository.record_step_failure(step.step_id, updated_at=active_now)
            except RepositoryStateError:
                failed = step
            self._message(task, step, error=error, now=active_now)
            self._trace(
                task,
                step,
                status="failed",
                error=error,
                duration_ms=round((time.monotonic() - started) * 1000),
                now=active_now,
            )
            return RuntimeStepResult(step=failed, status=failed.status, error=error)

    def _task(self, task_id: str) -> AgentTask:
        task = self.repository.get_task(task_id)
        if task is None:
            raise AgentRuntimeError(f"task does not exist: {task_id}")
        return task

    def _message(
        self,
        task: AgentTask,
        step: AgentStep,
        *,
        artifact: ArtifactRef | None = None,
        error: str | None = None,
        now: datetime,
    ) -> None:
        summary = f"{step.actor} step {step.step_id} {'completed' if error is None else 'failed'}"
        if error is not None:
            summary += f": {error}"
        self.repository.add_message(
            AgentMessage(
                message_id=f"message-runtime-{uuid4().hex}",
                task_id=task.task_id,
                sender=step.actor,
                recipient="orchestrator",
                summary=summary,
                artifact_refs=[artifact] if artifact is not None else [],
                created_at=now,
            )
        )

    def _trace(
        self,
        task: AgentTask,
        step: AgentStep,
        *,
        status: str,
        output_artifact_id: str | None = None,
        error: str | None = None,
        duration_ms: int = 0,
        now: datetime,
    ) -> None:
        insert_trace_chain(
            self.repository.connection,
            create_trace(
                trace_id=f"trace-runtime-{step.step_id}-{step.attempt}",
                module="v2_agent_runtime",
                input_ref={"task_id": task.task_id, "step_id": step.step_id, "role": step.actor},
                output_ref={"output_artifact_id": output_artifact_id},
                status=status,
                error_msg=error,
                created_at=now,
            ),
        )
        plan = self.repository.get_latest_plan(task.task_id)
        self.trace_recorder.record(
            AgentTrace(
                trace_id=f"trace-step-{step.step_id}-{step.attempt}",
                task_id=task.task_id,
                plan_id=plan.plan_id if plan is not None else None,
                step_id=step.step_id,
                parent_trace_id=f"trace-plan-{plan.plan_id}" if plan is not None else None,
                component="step",
                status="success" if status == "success" else "failed",
                duration_ms=duration_ms,
                input_ref={"role": step.actor, "attempt": step.attempt},
                output_ref={"output_artifact_id": output_artifact_id},
                error_message=error,
                created_at=now,
            )
        )


def _parse_input(payload: object | None, registration: AgentRegistration) -> object:
    if registration.input_schema is None:
        return payload
    if payload is None:
        raise AgentRuntimeError("step input is required before this Agent can run")
    try:
        return registration.input_schema.model_validate(payload)
    except ValidationError as exc:
        raise AgentRuntimeError("persisted step input does not match the role schema") from exc


def _parse_output(output: Any, registration: AgentRegistration) -> object:
    if registration.output_schema is None:
        return output
    try:
        return registration.output_schema.model_validate(output)
    except ValidationError as exc:
        raise AgentRuntimeError("Agent handler output does not match the role schema") from exc


def _json_payload(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise AgentRuntimeError("Agent output is not JSON serializable") from exc
    return value


def _extract_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("\n", 1)[0]
    return text.removeprefix("json\n")


def _estimate_tokens(value: str) -> int:
    """Use a conservative provider-neutral estimate until usage metadata is available."""

    return max(1, (len(value) + 3) // 4)


def _safe_error(error: Exception) -> str:
    return (redact_text(str(error)) or error.__class__.__name__).replace("\n", " ")[:500]


def _utc_now(value: datetime | None) -> datetime:
    active_now = value or datetime.now(UTC)
    if active_now.tzinfo is None:
        raise ValueError("runtime time must be timezone-aware")
    return active_now.astimezone(UTC)


__all__ = [
    "AgentRuntime",
    "AgentRuntimeContext",
    "AgentRuntimeError",
    "RuntimeModelClient",
    "RuntimeStepResult",
]

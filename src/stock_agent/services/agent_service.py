"""Single lifecycle service for submitted V2 research tasks."""

from __future__ import annotations

import sqlite3
import json
from datetime import UTC, datetime
from functools import wraps
from threading import RLock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from stock_agent.agents.orchestrator import Orchestrator, OrchestratorError
from stock_agent.agents.planner import PlanningContext
from stock_agent.agents.runtime import AgentRuntime, RuntimeStepResult
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.observability import AgentTrace, AgentTraceRecorder, BudgetLedger
from stock_agent.security.research_policy import ResearchSafetyPolicy, SafetyRequest
from stock_agent.signals.approval import ApprovalRequest, SignalApprovalService
from stock_agent.storage.task_repository import RepositoryStateError, TaskRepository


def _service_locked(method):
    """Serialize access to one SQLite-backed lifecycle service instance."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)

    return wrapped


class AgentServiceError(RuntimeError):
    """A caller attempted to bypass the V2 task lifecycle."""


class AgentService:
    """The only service-facing API for V2 research task lifecycle operations."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        runtime: AgentRuntime,
        orchestrator: Orchestrator | None = None,
        approval_service: SignalApprovalService | None = None,
    ) -> None:
        self.connection = connection
        self.lock = RLock()
        self.repository = TaskRepository(connection)
        self.runtime = runtime
        self.orchestrator = orchestrator or Orchestrator(connection)
        self.approval_service = approval_service
        self.safety_policy = ResearchSafetyPolicy(connection)
        self.budget_ledger = BudgetLedger(connection)
        self.trace_recorder = AgentTraceRecorder(connection)

    @_service_locked
    def submit(
        self,
        request: ResearchRequest,
        *,
        task_id: str | None = None,
        planning_context: PlanningContext | None = None,
        now: datetime | None = None,
    ) -> AgentTask:
        active_now = _utc_now(now)
        self._authorize_research(raw_text=request.question, now=active_now)
        identifier = task_id or f"task-v2-{uuid4().hex}"
        if self.repository.get_task(identifier) is not None:
            raise AgentServiceError(f"task already exists: {identifier}")
        task = AgentTask(task_id=identifier, request=request, created_at=active_now, updated_at=active_now)
        try:
            self.repository.create_task(task)
            self.budget_ledger.ensure(task, now=active_now)
            plan = self.orchestrator.start(identifier, planning_context or PlanningContext(), now=active_now)
            self.trace_recorder.record(
                AgentTrace(
                    trace_id=f"trace-task-{identifier}",
                    task_id=identifier,
                    component="task",
                    status="success",
                    input_ref={"request_id": request.request_id, "symbols": request.symbols, "report_type": request.report_type},
                    output_ref={"plan_id": plan.plan_id},
                    created_at=active_now,
                )
            )
            self.trace_recorder.record(
                AgentTrace(
                    trace_id=f"trace-plan-{plan.plan_id}",
                    task_id=identifier,
                    plan_id=plan.plan_id,
                    parent_trace_id=f"trace-task-{identifier}",
                    component="plan",
                    status="success",
                    input_ref={"revision": plan.revision},
                    output_ref={"step_ids": [step.step_id for step in plan.steps]},
                    created_at=active_now,
                )
            )
        except (RepositoryStateError, OrchestratorError, sqlite3.IntegrityError) as exc:
            raise AgentServiceError(str(exc)) from exc
        stored = self.repository.get_task(identifier)
        assert stored is not None
        return stored

    @_service_locked
    def get(self, task_id: str) -> dict[str, object]:
        task = self.repository.get_task(task_id)
        if task is None:
            raise AgentServiceError(f"task does not exist: {task_id}")
        plan = self.repository.get_latest_plan(task_id)
        return {
            "task": task.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json") if plan is not None else None,
            "messages": [message.model_dump(mode="json") for message in self.repository.list_messages(task_id)],
        }

    @_service_locked
    def provide_input(self, task_id: str, step_id: str, typed_input: BaseModel | dict[str, Any] | None, *, now: datetime | None = None) -> None:
        """Store typed input for a future or recovered step; it does not execute the step."""

        payload: object
        if isinstance(typed_input, BaseModel):
            payload = typed_input.model_dump(mode="json")
        else:
            payload = typed_input
        self._authorize_research(
            raw_text=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            task_id=task_id,
            now=_utc_now(now),
        )
        try:
            self.repository.save_step_input(task_id, step_id, payload, updated_at=_utc_now(now))
        except RepositoryStateError as exc:
            raise AgentServiceError(str(exc)) from exc

    @_service_locked
    def run_ready(
        self,
        task_id: str,
        *,
        worker_id: str,
        limit: int = 1,
        now: datetime | None = None,
    ) -> list[RuntimeStepResult]:
        task = self.repository.get_task(task_id)
        if task is None:
            raise AgentServiceError(f"task does not exist: {task_id}")
        self._authorize_research(raw_text=task.request.question, task_id=task_id, now=_utc_now(now))
        results = self.runtime.run_ready(task_id, worker_id=worker_id, limit=limit, now=now)
        self._complete_if_latest_plan_finished(task_id, now=now)
        return results

    @_service_locked
    def pause(self, task_id: str, *, now: datetime | None = None) -> AgentTask:
        return self._call_lifecycle(self.orchestrator.pause, task_id, now=now)

    @_service_locked
    def resume(self, task_id: str, *, now: datetime | None = None) -> AgentTask:
        task = self.repository.get_task(task_id)
        if task is None:
            raise AgentServiceError(f"task does not exist: {task_id}")
        self._authorize_research(raw_text=task.request.question, task_id=task_id, now=_utc_now(now))
        self.orchestrator.recover(task_id, now=now)
        return self._call_lifecycle(self.orchestrator.resume, task_id, now=now)

    @_service_locked
    def cancel(self, task_id: str, *, now: datetime | None = None) -> AgentTask:
        return self._call_lifecycle(self.orchestrator.cancel, task_id, now=now)

    @_service_locked
    def approve(self, task_id: str, request: ApprovalRequest, *, now: datetime | None = None) -> object:
        """Route the sole supported approval action to the human-only Signal boundary."""

        if self.repository.get_task(task_id) is None:
            raise AgentServiceError(f"task does not exist: {task_id}")
        if self.approval_service is None:
            raise AgentServiceError("signal approval is not configured for this service")
        decision = self.safety_policy.inspect(
            SafetyRequest(
                source="agent_service",
                actor_ref=request.decided_by,
                actor_type="human_admin" if request.actor_role == "admin" else "agent",
                requested_capability="approve_signal",
                raw_text=request.reason,
            )
        )
        if not decision.allowed:
            raise AgentServiceError(f"approval is blocked by policy: {decision.reason_code}")
        return self.approval_service.approve(request, now=now)

    def _complete_if_latest_plan_finished(self, task_id: str, *, now: datetime | None) -> None:
        task = self.repository.get_task(task_id)
        plan = self.repository.get_latest_plan(task_id)
        if task is None or plan is None or task.status != "running":
            return
        if all(step.status in {"succeeded", "skipped"} for step in plan.steps):
            try:
                self.repository.transition_task(
                    task_id,
                    expected_status="running",
                    new_status="completed",
                    updated_at=_utc_now(now),
                )
            except RepositoryStateError:
                return

    def _authorize_research(self, *, raw_text: str, now: datetime, task_id: str | None = None) -> None:
        decision = self.safety_policy.inspect(
            SafetyRequest(
                source="agent_service",
                actor_type="system",
                requested_capability="research",
                raw_text=raw_text,
            )
        )
        if decision.allowed:
            return
        if task_id is not None:
            self.trace_recorder.record(
                AgentTrace(
                    trace_id=f"trace-safety-{task_id}-{uuid4().hex}",
                    task_id=task_id,
                    component="task",
                    status="failed",
                    error_category="safety",
                    input_ref={"boundary": "agent_service"},
                    output_ref={"audit_id": decision.audit_id, "reason_code": decision.reason_code},
                    error_message=decision.reason_code,
                    created_at=now,
                )
            )
        audit_id = f" audit_id={decision.audit_id}" if decision.audit_id else ""
        raise AgentServiceError(f"research is blocked: {decision.reason_code}.{audit_id}")

    @staticmethod
    def _call_lifecycle(method, task_id: str, *, now: datetime | None) -> AgentTask:
        try:
            return method(task_id, now=now)
        except OrchestratorError as exc:
            raise AgentServiceError(str(exc)) from exc


def _utc_now(value: datetime | None) -> datetime:
    active_now = value or datetime.now(UTC)
    if active_now.tzinfo is None:
        raise ValueError("service time must be timezone-aware")
    return active_now.astimezone(UTC)


__all__ = ["AgentService", "AgentServiceError"]

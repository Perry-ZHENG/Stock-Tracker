"""Persistent orchestration authority for V2 multi-Agent research plans."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from stock_agent.agents.planner import AgentPlanner, PlanningContext, PlanningError
from stock_agent.contracts.evidence import EvidenceGapRequest
from stock_agent.contracts.tasks import AgentPlan, AgentStep, AgentTask
from stock_agent.storage.repositories import insert_trace_chain
from stock_agent.storage.task_repository import RepositoryStateError, TaskRepository
from stock_agent.tracing import create_trace


class OrchestratorError(RuntimeError):
    """Lifecycle requests that violate the durable task state machine."""


class Orchestrator:
    """The only V2 component allowed to create plans and Agent steps."""

    def __init__(self, connection: sqlite3.Connection, *, planner: AgentPlanner | None = None) -> None:
        self.connection = connection
        self.repository = TaskRepository(connection)
        self.planner = planner or AgentPlanner()

    def start(self, task_id: str, context: PlanningContext, *, now: datetime | None = None) -> AgentPlan:
        active_now = _utc_now(now)
        task = self._task(task_id)
        if task.status != "pending":
            raise OrchestratorError("only a pending task can receive its initial plan")
        try:
            plan = self.planner.build(task, context)
            self.repository.save_plan(plan, created_at=active_now)
            self.repository.transition_task(task_id, expected_status="pending", new_status="running", updated_at=active_now)
        except (PlanningError, RepositoryStateError) as exc:
            raise OrchestratorError(str(exc)) from exc
        self._trace(plan, action="plan_created", now=active_now)
        return plan

    def request_evidence(self, gap: EvidenceGapRequest, *, now: datetime | None = None) -> AgentPlan:
        active_now = _utc_now(now)
        task = self._task(gap.task_id)
        if task.status != "running":
            raise OrchestratorError("evidence gaps can only be planned for a running task")
        previous = self.repository.get_latest_plan(task.task_id)
        if previous is None:
            raise OrchestratorError("cannot replan a task without an initial plan")
        try:
            plan = self.planner.replan_for_gap(task, gap, previous_revision=previous.revision)
            self.repository.save_plan(plan, created_at=active_now)
        except (PlanningError, RepositoryStateError) as exc:
            raise OrchestratorError(str(exc)) from exc
        self._trace(plan, action="evidence_gap_replanned", now=active_now)
        return plan

    def claim_ready_steps(
        self,
        task_id: str,
        *,
        worker_id: str,
        limit: int = 1,
        now: datetime | None = None,
    ) -> list[AgentStep]:
        return self.repository.claim_ready_steps(
            task_id,
            worker_id=worker_id,
            limit=limit,
            claimed_at=_utc_now(now),
        )

    def record_step_failure(self, step_id: str, *, now: datetime | None = None) -> AgentStep:
        return self.repository.record_step_failure(step_id, updated_at=_utc_now(now))

    def recover(self, task_id: str, *, now: datetime | None = None) -> list[AgentStep]:
        task = self._task(task_id)
        if task.status not in {"running", "paused"}:
            return []
        return self.repository.recover_running_steps(task_id, updated_at=_utc_now(now))

    def pause(self, task_id: str, *, now: datetime | None = None) -> AgentTask:
        return self._transition(task_id, "running", "paused", now)

    def resume(self, task_id: str, *, now: datetime | None = None) -> AgentTask:
        return self._transition(task_id, "paused", "running", now)

    def cancel(self, task_id: str, *, now: datetime | None = None) -> AgentTask:
        active_now = _utc_now(now)
        current = self._task(task_id)
        if current.status not in {"running", "paused"}:
            raise OrchestratorError("only a running or paused task can be cancelled")
        task = self._transition(task_id, current.status, "cancelled", active_now)
        self.repository.cancel_open_steps(task_id, updated_at=active_now)
        return task

    def _transition(self, task_id: str, expected: str, target: str, now: datetime | None) -> AgentTask:
        try:
            return self.repository.transition_task(
                task_id,
                expected_status=expected,  # type: ignore[arg-type]
                new_status=target,  # type: ignore[arg-type]
                updated_at=_utc_now(now),
            )
        except RepositoryStateError as exc:
            raise OrchestratorError(str(exc)) from exc

    def _task(self, task_id: str) -> AgentTask:
        task = self.repository.get_task(task_id)
        if task is None:
            raise OrchestratorError(f"task {task_id} does not exist")
        return task

    def _trace(self, plan: AgentPlan, *, action: str, now: datetime) -> None:
        insert_trace_chain(
            self.connection,
            create_trace(
                trace_id=f"trace-orchestrator-{plan.plan_id}",
                module="v2_orchestrator",
                input_ref={"task_id": plan.task_id, "action": action},
                output_ref={"plan_id": plan.plan_id, "revision": plan.revision, "steps": [step.step_id for step in plan.steps]},
                created_at=now,
            ),
        )


def _utc_now(value: datetime | None) -> datetime:
    active_now = value or datetime.now(UTC)
    if active_now.tzinfo is None:
        raise ValueError("orchestrator time must be timezone-aware")
    return active_now.astimezone(UTC)


__all__ = ["Orchestrator", "OrchestratorError"]

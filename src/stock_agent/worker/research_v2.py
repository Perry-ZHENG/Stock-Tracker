"""Recoverable background execution for durable V2 research tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from stock_agent.contracts.evidence import EvidenceGapRequest
from stock_agent.services.agent_service import AgentService, AgentServiceError
@dataclass(frozen=True)
class ResearchWorkerTickV2:
    task_ids: list[str] = field(default_factory=list)
    executed_steps: int = 0
    replans: int = 0
    errors: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        return [
            "tick_status=research",
            f"tasks={len(self.task_ids)}",
            f"executed_steps={self.executed_steps}",
            f"replans={self.replans}",
            f"errors={len(self.errors)}",
        ]


class ResearchWorkerPipelineV2:
    """Run one durable V2 research-task tick without market-watch side effects."""

    def __init__(self, *, research_worker: "ResearchTaskWorkerV2") -> None:
        self.research_worker = research_worker

    def run_once(self) -> ResearchWorkerTickV2:
        return self.research_worker.run_once()


class ResearchTaskWorkerV2:
    """Drain ready V2 steps and replan only retryable evidence collectors."""

    def __init__(self, service: AgentService, *, worker_id: str, max_steps_per_task: int = 32) -> None:
        if not worker_id:
            raise ValueError("worker_id must be non-empty")
        self.service = service
        self.worker_id = worker_id
        self.max_steps_per_task = max_steps_per_task

    def run_once(self, *, now: datetime | None = None) -> ResearchWorkerTickV2:
        active_now = _utc_now(now)
        task_ids: list[str] = []
        errors: list[str] = []
        executed = 0
        replans = 0
        for task in self.service.repository.list_tasks(statuses=("running",)):
            tick = self.run_task(task.task_id, now=active_now)
            task_ids.extend(tick.task_ids)
            executed += tick.executed_steps
            replans += tick.replans
            errors.extend(tick.errors)
        return ResearchWorkerTickV2(task_ids=task_ids, executed_steps=executed, replans=replans, errors=errors)

    def run_task(self, task_id: str, *, now: datetime | None = None) -> ResearchWorkerTickV2:
        """Drain one named running task without consuming unrelated queued work."""

        active_now = _utc_now(now)
        task = self.service.repository.get_task(task_id)
        if task is None:
            return ResearchWorkerTickV2(errors=[f"{task_id}: task does not exist"])
        if task.status != "running":
            return ResearchWorkerTickV2(
                task_ids=[task_id],
                errors=[f"{task_id}: task is {task.status}, expected running"],
            )
        try:
            self.service.recover(task_id, now=active_now)
            executed, replans = self._drain_task(task_id, now=active_now)
        except AgentServiceError as exc:
            return ResearchWorkerTickV2(task_ids=[task_id], errors=[f"{task_id}: {exc}"])
        return ResearchWorkerTickV2(task_ids=[task_id], executed_steps=executed, replans=replans)

    def _drain_task(self, task_id: str, *, now: datetime) -> tuple[int, int]:
        executed = 0
        replans = 0
        for index in range(self.max_steps_per_task):
            results = self.service.run_ready(task_id, worker_id=f"{self.worker_id}:{index}", limit=1, now=now)
            executed += len(results)
            if self._replan_latest_gap(task_id, now=now):
                replans += 1
                continue
            if not results:
                break
        return executed, replans

    def _replan_latest_gap(self, task_id: str, *, now: datetime) -> bool:
        plan = self.service.repository.get_latest_plan(task_id)
        if plan is None:
            return False
        for step in plan.steps:
            if step.status != "succeeded":
                continue
            gap = _gap_from_step(self.service, task_id, step.step_id)
            if gap is None or not _is_retryable(gap):
                continue
            self.service.replan_for_evidence(gap, now=now)
            return True
        return False


def _gap_from_step(service: AgentService, task_id: str, step_id: str) -> EvidenceGapRequest | None:
    artifact_id = service.repository.get_step_output_artifact_id(task_id, step_id)
    if artifact_id is None:
        return None
    artifact = service.repository.get_artifact(task_id, artifact_id)
    if artifact is None:
        return None
    try:
        return EvidenceGapRequest.model_validate(service.runtime.artifact_service.load_json(task_id, artifact.ref))
    except Exception:
        return None


def _is_retryable(gap: EvidenceGapRequest) -> bool:
    """Configuration, model, and MCP gaps must wait for explicit human input."""

    if set(gap.missing_evidence_types) - {"bar", "news", "provider"}:
        return False
    reason = gap.reason.casefold()
    return not any(marker in reason for marker in ("configured modelclient", "allowlisted source", "explicit input"))


def _utc_now(value: datetime | None) -> datetime:
    active = value or datetime.now(UTC)
    if active.tzinfo is None:
        raise ValueError("worker time must be timezone-aware")
    return active.astimezone(UTC)


__all__ = ["ResearchTaskWorkerV2", "ResearchWorkerPipelineV2", "ResearchWorkerTickV2"]

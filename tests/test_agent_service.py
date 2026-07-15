from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_agent.agents.registry import AgentRegistration, AgentRegistry
from stock_agent.agents.runtime import AgentRuntime
from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.common import StrictSchema, TimeWindow
from stock_agent.contracts.tasks import ResearchRequest
from stock_agent.services.agent_service import AgentService, AgentServiceError
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)


class Output(StrictSchema):
    value: str


class NoInputHandler:
    def run(self, context, _typed_input) -> Output:
        return Output(value=context.step.actor)


def test_agent_service_owns_submission_lifecycle_and_restart(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    first = _service(connection, tmp_path)
    task = first.submit(_request(), task_id="task-service", now=NOW)
    assert task.status == "running"
    with pytest.raises(AgentServiceError, match="already exists"):
        first.submit(_request(), task_id="task-service", now=NOW)

    first.pause(task.task_id, now=NOW)
    assert first.run_ready(task.task_id, worker_id="paused", limit=20, now=NOW) == []
    first.resume(task.task_id, now=NOW)
    first.run_ready(task.task_id, worker_id="first", limit=20, now=NOW)

    restarted = _service(connection, tmp_path)
    for index in range(8):
        results = restarted.run_ready(task.task_id, worker_id=f"restart-{index}", limit=20, now=NOW)
        if not results:
            break
    current = restarted.get(task.task_id)

    assert current["task"]["status"] == "completed"
    assert len(current["messages"]) >= 3
    connection.close()


def test_agent_service_cancels_open_steps_and_never_auto_approves(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    service = _service(connection, tmp_path)
    task = service.submit(_request(), task_id="task-cancel", now=NOW)
    cancelled = service.cancel(task.task_id, now=NOW)

    assert cancelled.status == "cancelled"
    with pytest.raises(AgentServiceError, match="not configured"):
        service.approve(task.task_id, _approval_request(), now=NOW)
    connection.close()


def _service(connection, root: Path) -> AgentService:
    repository = TaskRepository(connection)
    artifacts = ArtifactService(ArtifactStore(connection, root / "lake"))
    registry = AgentRegistry()
    for role in ("orchestrator", "signal_discovery", "anomaly_analysis", "macro_analysis", "report"):
        registry.register(AgentRegistration(role=role, handler=NoInputHandler(), output_schema=Output))
    runtime = AgentRuntime(repository=repository, artifact_service=artifacts, registry=registry)
    return AgentService(connection, runtime=runtime)


def _request() -> ResearchRequest:
    return ResearchRequest(
        request_id="request-service",
        question="Create a bounded full report.",
        symbols=["QQQ"],
        time_window=TimeWindow(from_ts=NOW - timedelta(days=1), to_ts=NOW, timezone="America/New_York"),
        report_type="full",
    )


def _approval_request():
    from stock_agent.signals.approval import ApprovalRequest

    return ApprovalRequest(signal_id="signal-1", version=1, decided_by="admin", actor_role="admin", reason="manual review")

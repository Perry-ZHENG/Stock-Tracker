from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.agents.registry import AgentRegistration, AgentRegistry
from stock_agent.agents.runtime import AgentRuntime
from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.common import ExecutionBudget, StrictSchema, TimeWindow
from stock_agent.contracts.tasks import AgentPlan, AgentStep, AgentTask, ResearchRequest
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)


class Output(StrictSchema):
    value: str


class Handler:
    def run(self, _context, _typed_input) -> Output:
        return Output(value="completed")


def test_duration_budget_starts_when_worker_first_claims_the_task(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    repository = TaskRepository(connection)
    created_at = NOW - timedelta(hours=1)
    task = AgentTask(
        task_id="task-execution-clock",
        request=ResearchRequest(
            request_id="request-execution-clock",
            question="Create a bounded QQQ facts report.",
            symbols=["QQQ"],
            time_window=TimeWindow(from_ts=created_at - timedelta(days=1), to_ts=created_at, timezone="America/New_York"),
            report_type="facts",
        ),
        status="running",
        budget=ExecutionBudget(max_duration_seconds=60),
        created_at=created_at,
        updated_at=created_at,
    )
    repository.create_task(task)
    repository.save_plan(
        AgentPlan(
            plan_id="plan-execution-clock-r1",
            task_id=task.task_id,
            steps=[AgentStep(step_id="step-data", actor="orchestrator")],
            reason="fixture",
        ),
        created_at=created_at,
    )
    registry = AgentRegistry()
    registry.register(AgentRegistration(role="orchestrator", handler=Handler(), output_schema=Output))
    runtime = AgentRuntime(
        repository=repository,
        artifact_service=ArtifactService(ArtifactStore(connection, tmp_path / "lake")),
        registry=registry,
    )

    result = runtime.run_ready(task.task_id, worker_id="fixture", now=NOW)
    stored = repository.get_task(task.task_id)

    assert result[0].status == "succeeded"
    assert stored is not None and stored.execution_started_at == NOW
    connection.close()

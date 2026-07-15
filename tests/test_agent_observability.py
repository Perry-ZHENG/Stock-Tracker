from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_agent.agents.registry import AgentRegistration, AgentRegistry
from stock_agent.agents.runtime import AgentRuntime
from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.common import ExecutionBudget, StrictSchema, TimeWindow
from stock_agent.contracts.tasks import AgentPlan, AgentStep, AgentTask, ResearchRequest
from stock_agent.observability import AgentTrace, AgentTraceRecorder, BudgetExceeded, BudgetLedger
from stock_agent.query import QueryService
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)


class ModelOutput(StrictSchema):
    value: str


class ModelHandler:
    def run(self, context, _typed_input) -> ModelOutput:
        return context.call_model("private prompt: summarize news", ModelOutput)


def test_runtime_persists_model_trace_and_enforces_task_budget(tmp_path: Path) -> None:
    connection, repository, task = _task(tmp_path, max_model_calls=1)
    repository.save_plan(
        AgentPlan(
            plan_id="plan-observability",
            task_id=task.task_id,
            reason="model trace test",
            steps=[AgentStep(step_id="step-report", actor="report")],
        ),
        created_at=NOW,
    )
    registry = AgentRegistry()
    registry.register(
        AgentRegistration(
            role="report",
            handler=ModelHandler(),
            output_schema=ModelOutput,
            max_model_calls=1,
        )
    )
    runtime = AgentRuntime(
        repository=repository,
        artifact_service=ArtifactService(ArtifactStore(connection, tmp_path / "lake")),
        registry=registry,
        model_client=lambda _prompt: '{"value":"bounded"}',
    )

    result = runtime.run_ready(task.task_id, worker_id="worker", now=NOW)
    snapshot = BudgetLedger(connection).get(task.task_id)
    traces = AgentTraceRecorder(connection).list_task(task.task_id)

    assert result[0].status == "succeeded"
    assert snapshot is not None
    assert snapshot.used_model_calls == 1
    assert snapshot.input_tokens > 0
    assert {trace.component for trace in traces} >= {"model", "step"}
    with pytest.raises(BudgetExceeded):
        BudgetLedger(connection).consume(task.task_id, model_calls=1, now=NOW)
    connection.close()


def test_trace_queries_are_redacted_and_include_health_summary(tmp_path: Path) -> None:
    connection, _repository, task = _task(tmp_path, max_model_calls=2)
    recorder = AgentTraceRecorder(connection)
    recorder.record(
        AgentTrace(
            trace_id="trace-sensitive",
            task_id=task.task_id,
            component="tool",
            status="failed",
            input_ref={
                "prompt": "do not expose this news body",
                "file_path": "/Users/zpy/private/candidate.py",
                "request_id": "request-observability",
            },
            output_ref={"candidate_source": "def forbidden(): pass", "artifact_id": "artifact-1"},
            error_message="policy blocked tool request at /Users/zpy/private/candidate.py",
            created_at=NOW,
        )
    )
    connection.close()

    result = QueryService(tmp_path).execute("agent-trace", target_id=task.task_id)

    assert result.ok
    assert "queue_depth=0" in result.text
    assert "prompt" in result.text  # The schema key is useful, but its value is never returned.
    assert "do not expose" not in result.text
    assert "candidate.py" not in result.text
    stored = result.rows[0]
    assert stored.input_ref["prompt"] == "[REDACTED]"
    assert stored.output_ref["candidate_source"] == "[REDACTED]"


def _task(tmp_path: Path, *, max_model_calls: int):
    connection = initialize_runtime_database(tmp_path)
    repository = TaskRepository(connection)
    task = AgentTask(
        task_id="task-observability",
        request=ResearchRequest(
            request_id="request-observability",
            question="Create a bounded research report.",
            symbols=["QQQ"],
            time_window=TimeWindow(
                from_ts=NOW - timedelta(days=1),
                to_ts=NOW,
                timezone="America/New_York",
            ),
        ),
        status="running",
        budget=ExecutionBudget(max_model_calls=max_model_calls, max_tool_calls=2),
        created_at=NOW,
        updated_at=NOW,
    )
    repository.create_task(task)
    return connection, repository, task

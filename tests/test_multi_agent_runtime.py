from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import Field

from stock_agent.agents.registry import AgentRegistration, AgentRegistry
from stock_agent.agents.runtime import AgentRuntime
from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.common import StrictSchema, TimeWindow
from stock_agent.contracts.tasks import AgentPlan, AgentStep, AgentTask, ResearchRequest
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)


class RuntimeInput(StrictSchema):
    value: str = Field(min_length=1)


class RuntimeOutput(StrictSchema):
    value: str = Field(min_length=1)


class EchoHandler:
    def run(self, _context, typed_input: RuntimeInput) -> RuntimeOutput:
        return RuntimeOutput(value=typed_input.value)


class ModelHandler:
    def run(self, context, _typed_input) -> RuntimeOutput:
        return context.call_model("return the required JSON", RuntimeOutput)


class ForbiddenToolHandler:
    def run(self, context, _typed_input):
        context.require_tool("write_file")
        return RuntimeOutput(value="unreachable")


def test_runtime_executes_typed_steps_persists_messages_and_resumes(tmp_path: Path) -> None:
    connection, repository, service, task = _task(tmp_path)
    plan = AgentPlan(
        plan_id="plan-runtime",
        task_id=task.task_id,
        reason="test typed steps",
        steps=[
            AgentStep(step_id="step-signal", actor="signal_discovery"),
            AgentStep(step_id="step-anomaly", actor="anomaly_analysis", depends_on=["step-signal"]),
            AgentStep(step_id="step-report", actor="report", depends_on=["step-anomaly"]),
        ],
    )
    repository.save_plan(plan, created_at=NOW)
    for step in plan.steps:
        repository.save_step_input(task.task_id, step.step_id, {"value": step.step_id}, updated_at=NOW)

    runtime = AgentRuntime(repository=repository, artifact_service=service, registry=_registry(EchoHandler()))
    first = runtime.run_ready(task.task_id, worker_id="worker-a", now=NOW)
    restarted = AgentRuntime(repository=TaskRepository(connection), artifact_service=service, registry=_registry(EchoHandler()))
    second = restarted.run_ready(task.task_id, worker_id="worker-b", now=NOW)
    third = restarted.run_ready(task.task_id, worker_id="worker-b", now=NOW)

    assert [result.status for result in [*first, *second, *third]] == ["succeeded", "succeeded", "succeeded"]
    assert len(repository.list_messages(task.task_id)) == 3
    assert all(repository.get_step_output_artifact_id(task.task_id, step.step_id) for step in plan.steps)
    connection.close()


def test_runtime_repairs_one_schema_response_and_rejects_forbidden_tools(tmp_path: Path) -> None:
    connection, repository, service, task = _task(tmp_path)
    repair_plan = AgentPlan(
        plan_id="plan-repair",
        task_id=task.task_id,
        reason="model repair",
        steps=[AgentStep(step_id="step-report", actor="report")],
    )
    repository.save_plan(repair_plan, created_at=NOW)
    model_calls: list[str] = []

    def model(prompt: str) -> str:
        model_calls.append(prompt)
        return "{}" if len(model_calls) == 1 else '{"value":"repaired"}'

    runtime = AgentRuntime(
        repository=repository,
        artifact_service=service,
        registry=_registry(ModelHandler(), report_model_calls=2),
        model_client=model,
    )
    repaired = runtime.run_ready(task.task_id, worker_id="worker", now=NOW)

    blocked_plan = AgentPlan(
        plan_id="plan-blocked",
        task_id=task.task_id,
        revision=2,
        reason="tool boundary",
        steps=[AgentStep(step_id="step-macro", actor="macro_analysis")],
    )
    repository.save_plan(blocked_plan, created_at=NOW)
    blocked = AgentRuntime(
        repository=repository,
        artifact_service=service,
        registry=_registry(ForbiddenToolHandler()),
    ).run_ready(task.task_id, worker_id="worker", now=NOW)

    assert repaired[0].status == "succeeded"
    assert len(model_calls) == 2
    assert blocked[0].status == "failed"
    assert "not allowed" in (blocked[0].error or "")
    connection.close()


def _task(tmp_path: Path):
    connection = initialize_database(tmp_path / "runtime.sqlite")
    repository = TaskRepository(connection)
    task = AgentTask(
        task_id="task-runtime",
        request=ResearchRequest(
            request_id="request-runtime",
            question="Run a bounded research workflow.",
            symbols=["QQQ"],
            time_window=TimeWindow(from_ts=NOW - timedelta(days=1), to_ts=NOW, timezone="America/New_York"),
        ),
        status="running",
        created_at=NOW,
        updated_at=NOW,
    )
    repository.create_task(task)
    return connection, repository, ArtifactService(ArtifactStore(connection, tmp_path / "lake")), task


def _registry(handler, *, report_model_calls: int = 0) -> AgentRegistry:
    registry = AgentRegistry()
    for role in ("orchestrator", "signal_discovery", "anomaly_analysis", "macro_analysis", "report"):
        registry.register(
            AgentRegistration(
                role=role,
                handler=handler,
                input_schema=RuntimeInput if isinstance(handler, EchoHandler) else None,
                output_schema=RuntimeOutput,
                allowed_tools=frozenset({"data_evidence"}),
                max_model_calls=report_model_calls if role == "report" else 0,
            )
        )
    return registry

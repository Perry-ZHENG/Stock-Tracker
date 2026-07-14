from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_agent.agents.orchestrator import Orchestrator, OrchestratorError
from stock_agent.agents.planner import PlanningContext
from stock_agent.contracts.common import ExecutionBudget, TimeWindow
from stock_agent.contracts.evidence import EvidenceGapRequest
from stock_agent.contracts.signals import ExistingSignal
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("report_type", "active_signal", "expected"),
    [
        ("facts", False, {"step-data", "step-news", "step-report", "step-validator"}),
        ("anomaly", False, {"step-data", "step-news", "step-anomaly", "step-report", "step-validator"}),
        ("macro", False, {"step-data", "step-news", "step-macro", "step-report", "step-validator"}),
        ("signal", False, {"step-data", "step-news", "step-active-signals", "step-signal-discovery", "step-report", "step-validator"}),
        ("full", True, {"step-data", "step-news", "step-active-signals", "step-anomaly", "step-macro", "step-report", "step-validator"}),
    ],
)
def test_orchestrator_builds_minimal_report_type_dags(
    tmp_path: Path,
    report_type: str,
    active_signal: bool,
    expected: set[str],
) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    task = _task(f"task-{report_type}", report_type=report_type)
    TaskRepository(connection).create_task(task)
    context = PlanningContext(existing_signals=[_active_signal()] if active_signal else [])

    plan = Orchestrator(connection).start(task.task_id, context, now=NOW)

    by_id = {step.step_id: step for step in plan.steps}
    assert set(by_id) == expected
    assert by_id["step-report"].depends_on
    assert by_id["step-validator"].depends_on == ["step-report"]
    assert TaskRepository(connection).get_task(task.task_id).status == "running"  # type: ignore[union-attr]
    connection.close()


def test_orchestrator_unlocks_parallel_steps_retries_and_recovers_after_restart(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    task = _task("task-full", report_type="full")
    TaskRepository(connection).create_task(task)
    orchestrator = Orchestrator(connection)
    orchestrator.start(task.task_id, PlanningContext(existing_signals=[_active_signal()]), now=NOW)

    initial = orchestrator.claim_ready_steps(task.task_id, worker_id="worker", limit=2, now=NOW)
    assert {step.step_id for step in initial} == {"step-data", "step-news"}
    for step in initial:
        TaskRepository(connection).complete_step(step.step_id, expected_status="running", new_status="succeeded", updated_at=NOW)

    specialists = orchestrator.claim_ready_steps(task.task_id, worker_id="worker", limit=3, now=NOW)
    assert {step.step_id for step in specialists} == {"step-active-signals", "step-anomaly", "step-macro"}
    failed = next(step for step in specialists if step.step_id == "step-anomaly")
    retried = orchestrator.record_step_failure(failed.step_id, now=NOW)
    assert retried.status == "pending" and retried.attempt == 1
    recovered_claim = orchestrator.claim_ready_steps(task.task_id, worker_id="worker", limit=1, now=NOW)[0]
    assert recovered_claim.step_id == "step-anomaly" and recovered_claim.attempt == 2

    restarted = Orchestrator(connection)
    recovered = restarted.recover(task.task_id, now=NOW + timedelta(minutes=1))
    assert any(step.step_id == "step-anomaly" and step.status == "failed" for step in recovered)
    connection.close()


def test_orchestrator_pause_resume_cancel_and_evidence_gap_replan_are_bounded(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    task = _task("task-gap", report_type="anomaly")
    TaskRepository(connection).create_task(task)
    orchestrator = Orchestrator(connection)
    orchestrator.start(task.task_id, PlanningContext(), now=NOW)

    assert orchestrator.pause(task.task_id, now=NOW).status == "paused"
    assert orchestrator.claim_ready_steps(task.task_id, worker_id="worker", now=NOW) == []
    assert orchestrator.resume(task.task_id, now=NOW).status == "running"
    gap = EvidenceGapRequest(
        task_id=task.task_id,
        requester="anomaly_analysis",
        missing_evidence_types=["bar", "news"],
        reason="need comparable data and company news",
    )
    second = orchestrator.request_evidence(gap, now=NOW)
    third = orchestrator.request_evidence(gap, now=NOW)
    assert second.revision == 2 and third.revision == 3
    with pytest.raises(OrchestratorError, match="replan budget"):
        orchestrator.request_evidence(gap, now=NOW)

    assert orchestrator.cancel(task.task_id, now=NOW).status == "cancelled"
    assert all(step.status == "cancelled" for step in TaskRepository(connection).get_latest_plan(task.task_id).steps)  # type: ignore[union-attr]
    connection.close()


def test_orchestrator_rejects_plans_that_exceed_task_budget(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    task = _task(
        "task-budget",
        report_type="full",
        budget=ExecutionBudget(max_agent_steps=3, max_model_calls=1),
    )
    TaskRepository(connection).create_task(task)

    with pytest.raises(OrchestratorError, match="budget"):
        Orchestrator(connection).start(task.task_id, PlanningContext(), now=NOW)
    assert TaskRepository(connection).get_task(task.task_id).status == "pending"  # type: ignore[union-attr]
    connection.close()


def _task(task_id: str, *, report_type: str, budget: ExecutionBudget | None = None) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        request=ResearchRequest(
            request_id=f"request-{task_id}",
            question="Create a bounded research report.",
            symbols=["QQQ"],
            time_window=TimeWindow(
                from_ts=NOW - timedelta(days=1),
                to_ts=NOW,
                timezone="America/New_York",
            ),
            report_type=report_type,
        ),
        budget=budget or ExecutionBudget(),
        created_at=NOW,
        updated_at=NOW,
    )


def _active_signal() -> ExistingSignal:
    return ExistingSignal(
        signal_id="signal-active",
        version=1,
        name="existing volume confirmation",
        feature_fingerprint="volume-return-v1",
        status="active",
    )

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from stock_agent.agents.planner import AgentPlanner
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.tasks import AgentTask, ResearchRequest


def test_report_retry_plan_only_contains_report_and_validator_steps() -> None:
    now = datetime(2027, 1, 2, tzinfo=UTC)
    task = AgentTask(
        task_id="task-report-retry",
        request=ResearchRequest(
            request_id="request-report-retry",
            question="Create a bounded QQQ report.",
            symbols=["QQQ"],
            time_window=TimeWindow(from_ts=now - timedelta(days=1), to_ts=now, timezone="America/New_York"),
        ),
        status="running",
        created_at=now,
        updated_at=now,
    )

    plan = AgentPlanner().retry_report_after_validation(task, previous_revision=1)

    assert plan.revision == 2
    assert [step.step_id for step in plan.steps] == ["step-report-retry-r2", "step-validator-retry-r2"]
    assert plan.steps[1].depends_on == ["step-report-retry-r2"]


def test_report_retry_plan_allows_one_final_formatting_retry() -> None:
    now = datetime(2027, 1, 2, tzinfo=UTC)
    task = AgentTask(
        task_id="task-report-retry-final",
        request=ResearchRequest(
            request_id="request-report-retry-final",
            question="Create a bounded QQQ report.",
            symbols=["QQQ"],
            time_window=TimeWindow(from_ts=now - timedelta(days=1), to_ts=now, timezone="America/New_York"),
        ),
        status="running",
        created_at=now,
        updated_at=now,
    )

    plan = AgentPlanner().retry_report_after_validation(task, previous_revision=3)

    assert plan.revision == 4
    assert plan.steps[0].step_id == "step-report-retry-r4"

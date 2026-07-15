"""Deterministic V2 planner that turns one research task into a bounded DAG."""

from __future__ import annotations

from pydantic import Field

from stock_agent.agents.policies import AgentCapability, DEFAULT_AGENT_CAPABILITIES, OrchestrationPolicy
from stock_agent.contracts.common import StrictSchema
from stock_agent.contracts.evidence import EvidenceGapRequest, EvidenceRef
from stock_agent.contracts.signals import ExistingSignal
from stock_agent.contracts.tasks import AgentPlan, AgentStep, AgentTask


class PlanningError(ValueError):
    """The task cannot be planned without violating a hard orchestration constraint."""


class PlanningContext(StrictSchema):
    existing_signals: list[ExistingSignal] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    capabilities: list[AgentCapability] = Field(default_factory=lambda: list(DEFAULT_AGENT_CAPABILITIES))
    available_tools: list[str] = Field(default_factory=list)
    replan_count: int = Field(default=0, ge=0)


class AgentPlanner:
    """Only this component constructs AgentPlan instances for the Orchestrator."""

    def __init__(self, *, policy: OrchestrationPolicy | None = None) -> None:
        self.policy = policy or OrchestrationPolicy()

    def build(self, task: AgentTask, context: PlanningContext) -> AgentPlan:
        steps = _base_evidence_steps(self.policy)
        active_signals = [signal for signal in context.existing_signals if signal.status == "active"]
        report_type = task.request.report_type
        specialist_dependencies = ["step-data", "step-news"]
        report_dependencies = list(specialist_dependencies)

        if report_type in {"signal", "full"}:
            steps.append(_step("step-active-signals", "orchestrator", depends_on=["step-data"], input_refs=[signal.signal_id for signal in active_signals]))
            if not active_signals:
                _require_role("signal_discovery", context, report_type)
                steps.append(_step("step-signal-discovery", "signal_discovery", depends_on=specialist_dependencies))
                report_dependencies.append("step-signal-discovery")
            else:
                report_dependencies.append("step-active-signals")

        if report_type in {"anomaly", "full"}:
            _require_role("anomaly_analysis", context, report_type)
            steps.append(_step("step-anomaly", "anomaly_analysis", depends_on=specialist_dependencies))
            report_dependencies.append("step-anomaly")
        if report_type in {"macro", "full"}:
            _require_role("macro_analysis", context, report_type)
            steps.append(_step("step-macro", "macro_analysis", depends_on=specialist_dependencies))
            report_dependencies.append("step-macro")

        _require_role("report", context, report_type)
        steps.append(_step("step-report", "report", depends_on=report_dependencies))
        # Validation is an orchestrated deterministic gate, not a sixth LLM Agent.
        steps.append(_step("step-validator", "orchestrator", depends_on=["step-report"], input_refs=["validator:report"]))
        model_calls = sum(step.actor != "orchestrator" for step in steps)
        try:
            self.policy.validate_task_budget(task, planned_step_count=len(steps), planned_model_calls=model_calls)
        except ValueError as exc:
            raise PlanningError(str(exc)) from exc
        return AgentPlan(
            plan_id=f"plan-{task.task_id}-r1",
            task_id=task.task_id,
            steps=steps,
            revision=1,
            reason=f"{report_type} research requires evidence collection before specialist analysis and reporting",
        )

    def replan_for_gap(
        self,
        task: AgentTask,
        gap: EvidenceGapRequest,
        *,
        previous_revision: int,
    ) -> AgentPlan:
        if gap.task_id != task.task_id:
            raise PlanningError("evidence gap task does not match orchestrated task")
        if previous_revision >= self.policy.max_replans + 1:
            raise PlanningError("replan budget is exhausted")
        revision = previous_revision + 1
        steps: list[AgentStep] = []
        for evidence_type in sorted(set(gap.missing_evidence_types)):
            if evidence_type not in {"bar", "news", "provider", "mcp", "analysis"}:
                raise PlanningError(f"unsupported evidence gap type: {evidence_type}")
            step_id = f"step-gap-r{revision}-{evidence_type}"
            steps.append(_step(step_id, "orchestrator", input_refs=[f"gap:{evidence_type}"]))
        retry_dependencies = [step.step_id for step in steps]
        steps.append(_step(f"step-{gap.requester}-retry-r{revision}", gap.requester, depends_on=retry_dependencies))
        if len(steps) > task.budget.max_agent_steps:
            raise PlanningError("agent-step budget is insufficient for evidence-gap replan")
        return AgentPlan(
            plan_id=f"plan-{task.task_id}-r{revision}",
            task_id=task.task_id,
            steps=steps,
            revision=revision,
            reason=f"supplement only requested evidence for {gap.requester}: {gap.reason}",
        )

    def retry_report_after_validation(self, task: AgentTask, *, previous_revision: int) -> AgentPlan:
        """Retry only report wording after a deterministic validation rejection."""

        # Formatting-only report retries do not collect new evidence or invoke
        # tools, so allow one final bounded retry beyond evidence replanning.
        if previous_revision > self.policy.max_replans + 1:
            raise PlanningError("replan budget is exhausted")
        revision = previous_revision + 1
        report_step_id = f"step-report-retry-r{revision}"
        return AgentPlan(
            plan_id=f"plan-{task.task_id}-r{revision}",
            task_id=task.task_id,
            steps=[
                _step(report_step_id, "report"),
                _step(f"step-validator-retry-r{revision}", "orchestrator", depends_on=[report_step_id], input_refs=["validator:report"]),
            ],
            revision=revision,
            reason="retry report wording after deterministic validation rejection without recollecting evidence",
        )


def _base_evidence_steps(policy: OrchestrationPolicy) -> list[AgentStep]:
    return [
        _step("step-data", "orchestrator", input_refs=["workflow:data_evidence"]),
        _step("step-news", "orchestrator", input_refs=["workflow:news_evidence"]),
    ]


def _step(
    step_id: str,
    actor,
    *,
    depends_on: list[str] | None = None,
    input_refs: list[str] | None = None,
) -> AgentStep:
    return AgentStep(
        step_id=step_id,
        actor=actor,
        depends_on=depends_on or [],
        input_refs=input_refs or [],
        max_attempts=2,
    )


def _require_role(role, context: PlanningContext, report_type: str) -> None:
    if not OrchestrationPolicy().supports(role, context.capabilities):
        raise PlanningError(f"{report_type} plan requires enabled {role} Agent")


__all__ = ["AgentPlanner", "PlanningContext", "PlanningError"]

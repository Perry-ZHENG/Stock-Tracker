"""Static orchestration constraints; model output cannot add capabilities here."""

from __future__ import annotations

from pydantic import Field

from stock_agent.contracts.common import AgentRole, StrictSchema
from stock_agent.contracts.tasks import AgentTask


class AgentCapability(StrictSchema):
    role: AgentRole
    enabled: bool = True
    allowed_tools: list[str] = Field(default_factory=list)


class OrchestrationPolicy(StrictSchema):
    max_replans: int = 2
    step_max_attempts: int = 2
    require_news_for_report_types: list[str] = Field(
        default_factory=lambda: ["facts", "anomaly", "macro", "signal", "full"]
    )

    def validate_task_budget(self, task: AgentTask, *, planned_step_count: int, planned_model_calls: int) -> None:
        if planned_step_count > task.budget.max_agent_steps:
            raise ValueError("agent-step budget is insufficient for the required research plan")
        if planned_model_calls > task.budget.max_model_calls:
            raise ValueError("model-call budget is insufficient for the required research plan")

    def supports(self, role: AgentRole, capabilities: list[AgentCapability]) -> bool:
        return any(capability.role == role and capability.enabled for capability in capabilities)


DEFAULT_AGENT_CAPABILITIES = [
    AgentCapability(role="orchestrator", allowed_tools=["data_evidence", "news_evidence", "query_signals"]),
    AgentCapability(role="signal_discovery", allowed_tools=["data_evidence", "news_evidence", "query_signals"]),
    AgentCapability(role="anomaly_analysis", allowed_tools=["data_evidence", "news_evidence", "query_provider_compare"]),
    AgentCapability(role="macro_analysis", allowed_tools=["data_evidence", "news_evidence", "mcp"]),
    AgentCapability(role="report", allowed_tools=["evidence_bundle", "claim_validator"]),
]


__all__ = ["AgentCapability", "DEFAULT_AGENT_CAPABILITIES", "OrchestrationPolicy"]

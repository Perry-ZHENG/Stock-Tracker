"""Task planning, messages, and tool boundary contracts for V2 agents."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from stock_agent.contracts.common import (
    AgentRole,
    ExecutionBudget,
    StepStatus,
    StrictSchema,
    TaskStatus,
    TimeWindow,
    ensure_utc,
)
from stock_agent.contracts.evidence import ArtifactRef, EvidenceRef

ReportType = Literal["facts", "anomaly", "macro", "signal", "full"]
ToolStatus = Literal["succeeded", "failed", "rejected", "timed_out"]


class ResearchConstraints(StrictSchema):
    allow_mcp: bool = False
    allow_news_features: bool = False
    require_current_data: bool = False


class ResearchRequest(StrictSchema):
    request_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    symbols: list[str] = Field(min_length=1)
    time_window: TimeWindow
    report_type: ReportType = "full"
    constraints: ResearchConstraints = Field(default_factory=ResearchConstraints)

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, values: list[str]) -> list[str]:
        normalized = [value.upper() for value in values]
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("symbols must be non-empty and unique")
        return normalized


class AgentTask(StrictSchema):
    task_id: str = Field(min_length=1)
    request: ResearchRequest
    status: TaskStatus = "pending"
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)
    created_at: datetime
    execution_started_at: datetime | None = None
    updated_at: datetime

    @field_validator("created_at", "execution_started_at", "updated_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _validate_update_time(self) -> "AgentTask":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        if self.execution_started_at is not None and self.execution_started_at < self.created_at:
            raise ValueError("execution_started_at must not be earlier than created_at")
        return self


class AgentStep(StrictSchema):
    step_id: str = Field(min_length=1)
    actor: AgentRole
    depends_on: list[str] = Field(default_factory=list)
    input_refs: list[str] = Field(default_factory=list)
    status: StepStatus = "pending"
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=1, ge=1, le=10)

    @model_validator(mode="after")
    def _validate_attempts(self) -> "AgentStep":
        if self.attempt > self.max_attempts:
            raise ValueError("attempt cannot exceed max_attempts")
        if self.step_id in self.depends_on:
            raise ValueError("a step cannot depend on itself")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("depends_on must not contain duplicates")
        return self


class AgentPlan(StrictSchema):
    plan_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    steps: list[AgentStep] = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_dag(self) -> "AgentPlan":
        steps_by_id = {step.step_id: step for step in self.steps}
        if len(steps_by_id) != len(self.steps):
            raise ValueError("plan step_ids must be unique")
        unknown = {
            dependency
            for step in self.steps
            for dependency in step.depends_on
            if dependency not in steps_by_id
        }
        if unknown:
            raise ValueError(f"plan has unknown dependencies: {sorted(unknown)}")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visited:
                return
            if step_id in visiting:
                raise ValueError("plan dependencies must not contain a cycle")
            visiting.add(step_id)
            for dependency in steps_by_id[step_id].depends_on:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in steps_by_id:
            visit(step_id)
        return self


class AgentMessage(StrictSchema):
    message_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    sender: AgentRole | Literal["user", "system", "tool"]
    recipient: AgentRole | Literal["orchestrator", "system"]
    summary: str = Field(min_length=1, max_length=8_000)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]


class ToolRequest(StrictSchema):
    call_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    caller: AgentRole
    deadline_at: datetime

    @field_validator("deadline_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]


class ToolError(StrictSchema):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ToolResult(StrictSchema):
    call_id: str = Field(min_length=1)
    status: ToolStatus
    summary: str = Field(min_length=1)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    error: ToolError | None = None
    retryable: bool = False

    @model_validator(mode="after")
    def _validate_error(self) -> "ToolResult":
        if self.status == "succeeded" and self.error is not None:
            raise ValueError("a succeeded ToolResult cannot contain an error")
        if self.status != "succeeded" and self.error is None:
            raise ValueError("a failed ToolResult must contain an error")
        return self


__all__ = [
    "AgentMessage",
    "AgentPlan",
    "AgentStep",
    "AgentTask",
    "ResearchConstraints",
    "ResearchRequest",
    "ReportType",
    "ToolError",
    "ToolRequest",
    "ToolResult",
    "ToolStatus",
]

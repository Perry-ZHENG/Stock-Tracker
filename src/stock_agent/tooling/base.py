"""Typed Tool contracts and adapters for the V2 research runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, JsonValue

from stock_agent.config_loader import RuntimeConfigContext
from stock_agent.contracts.common import AgentRole, StrictSchema
from stock_agent.contracts.evidence import EvidenceRef

ToolPermission = Literal["read_only", "research_write", "approval_required", "forbidden"]
ToolSource = Literal["local", "mcp"]


class ToolDescriptor(StrictSchema):
    """Static capability metadata validated before an adapter is registered."""

    name: str = Field(min_length=1, max_length=160, pattern=r"^[a-z][a-z0-9_.-]*$")
    description: str = Field(min_length=1, max_length=4_000)
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)
    allowed_roles: list[AgentRole] = Field(min_length=1)
    permission: ToolPermission = "read_only"
    source: ToolSource = "local"


class ToolAdapterResponse(StrictSchema):
    """A normalized adapter result before the Gateway adds budget and artifacts."""

    summary: str = Field(min_length=1, max_length=4_000)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    untrusted: bool = False


class ToolCallBudget(StrictSchema):
    """Per-task Tool budget snapshot; persistence is owned by the future runtime."""

    max_tool_calls: int = Field(ge=0, le=500)
    used_tool_calls: int = Field(default=0, ge=0, le=500)

    @property
    def remaining_tool_calls(self) -> int:
        return self.max_tool_calls - self.used_tool_calls

    def consume(self) -> "ToolCallBudget":
        if self.used_tool_calls >= self.max_tool_calls:
            raise ToolBudgetExhausted("tool-call budget is exhausted")
        return self.model_copy(update={"used_tool_calls": self.used_tool_calls + 1})


class ToolExecutionContext(StrictSchema):
    """Trusted execution context supplied by the runtime, never by a Tool payload."""

    task_id: str = Field(min_length=1)
    actor: AgentRole
    budget: ToolCallBudget
    allow_mcp: bool = False
    entry_source: Literal["cli", "telegram", "fastapi"] | None = None
    entry_actor_ref: str | None = Field(default=None, max_length=256)


@dataclass(frozen=True)
class ToolRuntimeContext:
    """Non-serializable runtime values kept outside model-controlled arguments."""

    execution: ToolExecutionContext
    root: Path
    config_context: RuntimeConfigContext
    deadline_at: datetime


class ToolAdapter(Protocol):
    """Local and MCP adapters share one validation and invocation boundary."""

    @property
    def descriptor(self) -> ToolDescriptor: ...

    def validate_arguments(self, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]: ...

    def invoke(self, context: ToolRuntimeContext, arguments: dict[str, JsonValue]) -> ToolAdapterResponse: ...


class ToolAdapterError(RuntimeError):
    """A controlled adapter failure safe to expose as a ToolResult error."""


class ToolAdapterTimeout(ToolAdapterError):
    """The adapter exceeded its deadline and cancellation was requested."""


class ToolBudgetExhausted(ToolAdapterError):
    """The task has no remaining Tool calls."""


class ToolArgumentError(ToolAdapterError):
    """Arguments did not conform to the registered Tool schema."""


__all__ = [
    "ToolAdapter",
    "ToolAdapterError",
    "ToolAdapterResponse",
    "ToolAdapterTimeout",
    "ToolArgumentError",
    "ToolBudgetExhausted",
    "ToolCallBudget",
    "ToolDescriptor",
    "ToolExecutionContext",
    "ToolPermission",
    "ToolRuntimeContext",
    "ToolSource",
]

"""Typed role registry for the V2 multi-Agent runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel

from stock_agent.contracts.common import AgentRole

if TYPE_CHECKING:
    from stock_agent.agents.runtime import AgentRuntimeContext


class AgentHandler(Protocol):
    """A role-specific boundary; handlers receive only runtime-scoped context."""

    def run(self, context: "AgentRuntimeContext", typed_input: Any) -> Any: ...


@dataclass(frozen=True)
class AgentRegistration:
    """Static permissions and typed I/O requirements for one Agent role."""

    role: AgentRole
    handler: AgentHandler
    input_schema: type[BaseModel] | None = None
    output_schema: type[BaseModel] | None = None
    prompt_name: str = ""
    allowed_tools: frozenset[str] = frozenset()
    max_model_calls: int = 1
    max_context_characters: int = 24_000

    def __post_init__(self) -> None:
        if self.max_model_calls < 0:
            raise ValueError("max_model_calls must not be negative")
        if self.max_context_characters < 1:
            raise ValueError("max_context_characters must be positive")


class AgentRegistry:
    """One explicit registration per supported role; no model-selected roles."""

    def __init__(self) -> None:
        self._registrations: dict[AgentRole, AgentRegistration] = {}

    def register(self, registration: AgentRegistration) -> None:
        if registration.role in self._registrations:
            raise ValueError(f"role is already registered: {registration.role}")
        self._registrations[registration.role] = registration

    def get(self, role: AgentRole) -> AgentRegistration:
        try:
            return self._registrations[role]
        except KeyError as exc:
            raise KeyError(f"no Agent handler is registered for role: {role}") from exc

    def roles(self) -> tuple[AgentRole, ...]:
        return tuple(sorted(self._registrations))  # type: ignore[return-value]


__all__ = ["AgentHandler", "AgentRegistration", "AgentRegistry"]

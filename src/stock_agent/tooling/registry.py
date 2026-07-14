"""Allowlist registry for V2 local and MCP Tool adapters."""

from __future__ import annotations

from stock_agent.security.research_policy import ResearchSafetyPolicy, SafetyRequest
from stock_agent.tooling.base import ToolAdapter, ToolDescriptor


class ToolRegistrationError(ValueError):
    """A Tool failed duplicate, capability, or untrusted-description checks."""


class ToolRegistry:
    """Registry is an explicit allowlist; unknown names never reach adapters."""

    def __init__(self, adapters: list[ToolAdapter] | None = None) -> None:
        self._adapters: dict[str, ToolAdapter] = {}
        for adapter in adapters or []:
            self.register(adapter)

    def register(self, adapter: ToolAdapter) -> None:
        descriptor = adapter.descriptor
        if descriptor.name in self._adapters:
            raise ToolRegistrationError(f"duplicate tool name: {descriptor.name}")
        self._validate_descriptor(descriptor)
        self._adapters[descriptor.name] = adapter

    def get(self, name: str) -> ToolAdapter | None:
        return self._adapters.get(name)

    def descriptors(self) -> list[ToolDescriptor]:
        return [self._adapters[name].descriptor for name in sorted(self._adapters)]

    def names(self) -> list[str]:
        return sorted(self._adapters)

    @staticmethod
    def _validate_descriptor(descriptor: ToolDescriptor) -> None:
        policy = ResearchSafetyPolicy()
        decision = policy.decide(
            SafetyRequest(
                source=descriptor.source,
                actor_type="tool",
                requested_capability="use_mcp" if descriptor.source == "mcp" else "research",
                raw_text=f"{descriptor.name}\n{descriptor.description}",
                input_trust="untrusted" if descriptor.source == "mcp" else "trusted",
                untrusted_text=descriptor.description if descriptor.source == "mcp" else None,
                tool_name=descriptor.name,
            )
        )
        if not decision.allowed:
            raise ToolRegistrationError(
                f"tool {descriptor.name} is blocked by research safety policy: {decision.reason_code}"
            )


__all__ = ["ToolRegistrationError", "ToolRegistry"]

"""Compatibility adapter that lifts existing AgentTool handlers into the V2 boundary."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from stock_agent.agent.tools import AgentTool, AgentToolContext
from stock_agent.security.redaction import redact_sensitive, redact_text
from stock_agent.tooling.base import (
    ToolAdapterError,
    ToolAdapterResponse,
    ToolArgumentError,
    ToolDescriptor,
    ToolRuntimeContext,
)


class LegacyAgentToolAdapter:
    """Preserve existing read-only handlers while removing raw observations from V2 callers."""

    def __init__(self, tool: AgentTool, descriptor: ToolDescriptor) -> None:
        self.tool = tool
        self._descriptor = descriptor

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def validate_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            validated = self.tool.args_model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolArgumentError("tool arguments did not match the registered schema") from exc
        return validated.model_dump(mode="json")

    def invoke(self, context: ToolRuntimeContext, arguments: dict[str, Any]) -> ToolAdapterResponse:
        try:
            observation = self.tool.invoke(
                AgentToolContext(root=context.root, config_context=context.config_context),
                arguments,
            )
        except Exception as exc:  # pragma: no cover - existing external provider boundary
            raise ToolAdapterError(redact_text(str(exc)) or "legacy tool failed") from exc
        payload = _jsonable(redact_sensitive(observation))
        if not isinstance(payload, dict):
            raise ToolAdapterError("legacy tool returned a non-object observation")
        summary = str(payload.get("message") or payload.get("status") or f"{self.descriptor.name} completed")
        return ToolAdapterResponse(
            summary=redact_text(summary) or f"{self.descriptor.name} completed",
            payload=payload,
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = ["LegacyAgentToolAdapter"]

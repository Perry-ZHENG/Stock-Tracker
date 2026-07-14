"""Allowlisted, synchronous MCP client boundary for V2 Tool adapters."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from pydantic import Field, JsonValue

from stock_agent.security.redaction import redact_sensitive, redact_text
from stock_agent.tooling.base import (
    ToolAdapterError,
    ToolAdapterResponse,
    ToolAdapterTimeout,
    ToolArgumentError,
    ToolDescriptor,
    ToolRuntimeContext,
)

_SERVER_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class McpTransport(Protocol):
    """Small transport surface that real SDK and fake test transports can implement."""

    def list_tools(self) -> list[dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, JsonValue], *, call_id: str) -> Any: ...

    def cancel(self, call_id: str) -> None: ...


class McpToolDefinition(ToolDescriptor):
    """Cached, namespaced MCP Tool metadata after allowlist filtering."""

    remote_name: str = Field(min_length=1, max_length=160)


class McpClientError(ToolAdapterError):
    """The remote MCP transport failed, disconnected, or returned invalid data."""


class McpClient:
    """Discover and invoke only explicitly allowed MCP Tools from one server."""

    def __init__(
        self,
        *,
        server_name: str,
        transport: McpTransport,
        allowed_tools: set[str],
        timeout_seconds: float = 15.0,
        allowed_roles: list[str] | None = None,
    ) -> None:
        if not _SERVER_NAME.fullmatch(server_name):
            raise ValueError("server_name must use lowercase letters, digits, and hyphens")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.server_name = server_name
        self.transport = transport
        self.allowed_tools = set(allowed_tools)
        self.timeout_seconds = timeout_seconds
        self.allowed_roles = allowed_roles or [
            "orchestrator",
            "signal_discovery",
            "anomaly_analysis",
            "macro_analysis",
            "report",
        ]
        self._definitions: dict[str, McpToolDefinition] | None = None

    def discover(self, *, refresh: bool = False) -> list[McpToolDefinition]:
        """Fetch and cache allowlisted definitions under the ``mcp.server.name`` namespace."""

        if self._definitions is not None and not refresh:
            return [self._definitions[name] for name in sorted(self._definitions)]
        try:
            raw_tools = self.transport.list_tools()
        except Exception as exc:  # pragma: no cover - transport-specific failure
            raise McpClientError("MCP discovery failed") from exc
        definitions: dict[str, McpToolDefinition] = {}
        for raw_tool in raw_tools:
            remote_name = raw_tool.get("name")
            if not isinstance(remote_name, str) or remote_name not in self.allowed_tools:
                continue
            description = raw_tool.get("description")
            input_schema = raw_tool.get("input_schema", raw_tool.get("inputSchema", {}))
            if not isinstance(description, str) or not isinstance(input_schema, dict):
                raise McpClientError(f"MCP tool {remote_name!r} has invalid metadata")
            namespaced_name = f"mcp.{self.server_name}.{remote_name}"
            definitions[namespaced_name] = McpToolDefinition(
                name=namespaced_name,
                remote_name=remote_name,
                description=redact_text(description) or "MCP tool",
                input_schema=redact_sensitive(input_schema),
                allowed_roles=self.allowed_roles,
                permission="read_only",
                source="mcp",
            )
        self._definitions = definitions
        return [definitions[name] for name in sorted(definitions)]

    def adapters(self) -> list["McpToolAdapter"]:
        return [McpToolAdapter(self, definition) for definition in self.discover()]

    def call(self, definition: McpToolDefinition, arguments: dict[str, JsonValue], *, deadline_at: datetime) -> Any:
        remaining = min(self.timeout_seconds, (deadline_at - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            raise ToolAdapterTimeout("MCP deadline has already elapsed")
        call_id = f"mcp-{uuid4().hex}"
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stock-agent-mcp")
        future = executor.submit(self.transport.call_tool, definition.remote_name, arguments, call_id=call_id)
        try:
            result = future.result(timeout=remaining)
        except FutureTimeoutError as exc:
            future.cancel()
            cancel = getattr(self.transport, "cancel", None)
            if callable(cancel):
                try:
                    cancel(call_id)
                except Exception:
                    pass
            executor.shutdown(wait=False, cancel_futures=True)
            raise ToolAdapterTimeout("MCP tool timed out and cancellation was requested") from exc
        except Exception as exc:
            executor.shutdown(wait=True, cancel_futures=True)
            raise McpClientError("MCP tool call failed") from exc
        executor.shutdown(wait=True, cancel_futures=True)
        return result


class McpToolAdapter:
    """Expose one allowlisted MCP Tool through the shared ToolAdapter protocol."""

    def __init__(self, client: McpClient, definition: McpToolDefinition) -> None:
        self.client = client
        self._descriptor = definition

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def validate_arguments(self, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            _validate_json_schema(self._descriptor.input_schema, arguments)
        except ValueError as exc:
            raise ToolArgumentError("MCP tool arguments did not match the cached schema") from exc
        return arguments

    def invoke(self, context: ToolRuntimeContext, arguments: dict[str, JsonValue]) -> ToolAdapterResponse:
        result = self.client.call(self._descriptor, arguments, deadline_at=context.deadline_at)
        payload = _normalize_payload(result)
        summary = str(payload.get("summary") or payload.get("message") or "MCP tool completed")
        return ToolAdapterResponse(
            summary=redact_text(summary) or "MCP tool completed",
            payload=payload,
            untrusted=True,
        )


def _validate_json_schema(schema: dict[str, JsonValue], arguments: dict[str, JsonValue]) -> None:
    if schema.get("type") not in {None, "object"}:
        raise ValueError("MCP input schema must describe an object")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise ValueError("MCP input schema has invalid required fields")
    missing = [field for field in required if field not in arguments]
    if missing:
        raise ValueError(f"missing required arguments: {missing}")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("MCP input schema properties must be an object")
    if schema.get("additionalProperties") is False:
        unexpected = set(arguments) - set(properties)
        if unexpected:
            raise ValueError(f"unexpected arguments: {sorted(unexpected)}")
    for name, value in arguments.items():
        property_schema = properties.get(name)
        if isinstance(property_schema, dict):
            _validate_json_value(name, value, property_schema)


def _validate_json_value(name: str, value: JsonValue, schema: dict[str, JsonValue]) -> None:
    expected = schema.get("type")
    if expected == "string" and not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError(f"{name} must be an integer")
    if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise ValueError(f"{name} must be a number")
    if expected == "boolean" and not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    if expected == "array" and not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    if expected == "object" and not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")


def _normalize_payload(result: Any) -> dict[str, JsonValue]:
    if isinstance(result, dict):
        payload = redact_sensitive(result)
    elif isinstance(result, str):
        payload = {"content": redact_text(result) or ""}
    else:
        try:
            payload = json.loads(json.dumps(redact_sensitive(result), ensure_ascii=False, default=str))
        except (TypeError, ValueError) as exc:
            raise McpClientError("MCP response cannot be normalized to JSON") from exc
        if not isinstance(payload, dict):
            payload = {"content": payload}
    return payload


__all__ = [
    "McpClient",
    "McpClientError",
    "McpToolAdapter",
    "McpToolDefinition",
    "McpTransport",
]

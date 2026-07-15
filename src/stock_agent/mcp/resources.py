"""Static, path-free MCP resources for Stock Agent capability discovery."""

from __future__ import annotations

from stock_agent.contracts.common import StrictSchema


class McpResource(StrictSchema):
    uri: str
    name: str
    mime_type: str = "application/json"
    description: str


_RESOURCES = (
    McpResource(uri="stock-agent://capabilities", name="capabilities", description="Read-only research capabilities and safety boundary."),
    McpResource(uri="stock-agent://schemas", name="schemas", description="JSON schemas accepted by public read-only tools."),
    McpResource(uri="stock-agent://version", name="version", description="MCP protocol and application version metadata."),
)


def list_resources() -> list[McpResource]:
    return list(_RESOURCES)


__all__ = ["McpResource", "list_resources"]

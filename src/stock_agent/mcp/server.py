"""Read-only JSON-RPC/stdio MCP server for Stock Agent research outputs."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import BoundedSemaphore
from typing import Callable, TextIO
from uuid import uuid4

from pydantic import JsonValue

from stock_agent import __version__
from stock_agent.artifacts.service import ArtifactService
from stock_agent.contracts.evidence import EvidenceBundle
from stock_agent.evidence.service import EvidenceService
from stock_agent.mcp.resources import McpResource, list_resources
from stock_agent.query.service import QueryService
from stock_agent.security.redaction import redact_sensitive, redact_text
from stock_agent.security.research_policy import ResearchSafetyPolicy, SafetyRequest
from stock_agent.storage.report_repository import ReportRepository
from stock_agent.storage.repositories import insert_trace_chain
from stock_agent.storage.signal_repository import SignalRepository
from stock_agent.tracing import create_trace

_FORBIDDEN_TERMS = frozenset({"trade", "order", "approve", "shell", "write", "file", "prompt", "source"})
_ABSOLUTE_PATH = re.compile(r"(?:^|[\s\"'])/(?:[^\s\"']+)")


class McpServerError(ValueError):
    """A caller requested an unavailable or invalid read-only MCP operation."""


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str
    input_schema: dict[str, JsonValue]
    handler: Callable[[dict[str, JsonValue]], dict[str, JsonValue]]


class StockAgentMcpServer:
    """Expose persisted research information through a strict read-only API."""

    def __init__(
        self,
        *,
        root: Path,
        connection: sqlite3.Connection,
        artifact_service: ArtifactService | None = None,
        max_concurrency: int = 4,
        safety_policy: ResearchSafetyPolicy | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self.root = root.resolve()
        self.connection = connection
        self.artifact_service = artifact_service
        self.safety_policy = safety_policy or ResearchSafetyPolicy(connection)
        self._semaphore = BoundedSemaphore(max_concurrency)
        self._tools = {tool.name: tool for tool in self._build_tools()}

    def initialize(self, _params: dict[str, JsonValue] | None = None) -> dict[str, JsonValue]:
        return {
            "protocolVersion": "2025-03-26",
            "serverInfo": {"name": "stock-agent", "version": __version__},
            "capabilities": {"tools": {}, "resources": {}},
        }

    def list_tools(self) -> list[dict[str, JsonValue]]:
        return [
            {"name": tool.name, "description": tool.description, "inputSchema": tool.input_schema}
            for tool in sorted(self._tools.values(), key=lambda item: item.name)
        ]

    def tools_list(self) -> dict[str, JsonValue]:
        return {"tools": self.list_tools()}

    def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue] | None = None,
        *,
        call_id: str | None = None,
    ) -> dict[str, JsonValue]:
        tool = self._tools.get(name)
        if tool is None or any(term in name.casefold() for term in _FORBIDDEN_TERMS):
            raise McpServerError("tool is not available from this read-only MCP server")
        args = arguments or {}
        _validate_object(args, tool.input_schema)
        safety = self.safety_policy.inspect(
            SafetyRequest(
                source="mcp_server",
                actor_type="tool",
                requested_capability="use_mcp",
                input_trust="untrusted",
                untrusted_text=json.dumps(args, ensure_ascii=False, sort_keys=True),
                tool_name=name,
                tool_arguments=args,
            )
        )
        if not safety.allowed:
            self._audit(name, args, {"error": "safety_blocked", "audit_id": safety.audit_id}, call_id=call_id)
            raise McpServerError("MCP request is blocked by research safety policy")
        if not self._semaphore.acquire(blocking=False):
            raise McpServerError("MCP server concurrency limit is reached")
        try:
            payload = _safe_payload(tool.handler(args))
            self._audit(name, args, payload, call_id=call_id)
            return {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}],
                "structuredContent": payload,
            }
        except McpServerError:
            raise
        except Exception as exc:  # pragma: no cover - storage boundary
            self._audit(name, args, {"error": "query_failed"}, call_id=call_id, error=exc)
            raise McpServerError("read-only MCP query failed") from exc
        finally:
            self._semaphore.release()

    def resources_list(self) -> dict[str, JsonValue]:
        return {"resources": [resource.model_dump(by_alias=True) for resource in list_resources()]}

    def read_resource(self, uri: str) -> dict[str, JsonValue]:
        resource = next((item for item in list_resources() if item.uri == uri), None)
        if resource is None:
            raise McpServerError("resource is not available")
        payload = self._resource_payload(resource)
        return {"contents": [{"uri": resource.uri, "mimeType": resource.mime_type, "text": json.dumps(_safe_payload(payload), ensure_ascii=False, sort_keys=True)}]}

    def handle_request(self, request: dict[str, object]) -> dict[str, JsonValue]:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return _jsonrpc_error(request_id, -32600, "invalid request")
        try:
            if method == "initialize":
                result = self.initialize(params)  # type: ignore[arg-type]
            elif method == "tools/list":
                result = self.tools_list()
            elif method == "tools/call":
                result = self.call_tool(str(params.get("name", "")), params.get("arguments", {}), call_id=str(request_id))  # type: ignore[arg-type]
            elif method == "resources/list":
                result = self.resources_list()
            elif method == "resources/read":
                result = self.read_resource(str(params.get("uri", "")))
            else:
                return _jsonrpc_error(request_id, -32601, "method not found")
        except (McpServerError, ValueError):
            return _jsonrpc_error(request_id, -32602, "request rejected")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _build_tools(self) -> list[_Tool]:
        limit_schema: dict[str, JsonValue] = {"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False}
        return [
            _Tool("market.bars", "Read stored market bars for explicit symbol and time range.", {"type": "object", "properties": {"symbol": {"type": "string"}, "from": {"type": "string"}, "to": {"type": "string"}}, "required": ["symbol", "from", "to"], "additionalProperties": False}, self._market_bars),
            _Tool("research.news", "Read cached news summaries only.", {**limit_schema, "properties": {"limit": {"type": "integer"}, "symbol": {"type": "string"}}}, self._news),
            _Tool("signal.active", "List active versions and non-trading observations.", {"type": "object", "properties": {"signal_id": {"type": "string"}, "version": {"type": "integer"}}, "additionalProperties": False}, self._active_signals),
            _Tool("research.evidence_bundle", "Read task-scoped registered evidence metadata.", {"type": "object", "properties": {"task_id": {"type": "string"}, "evidence_ids": {"type": "array"}}, "required": ["task_id", "evidence_ids"], "additionalProperties": False}, self._evidence_bundle),
            _Tool("research.analysis", "Read one persisted anomaly or macro analysis.", {"type": "object", "properties": {"analysis_id": {"type": "string"}}, "required": ["analysis_id"], "additionalProperties": False}, self._analysis),
            _Tool("research.report", "Read one persisted draft or validated final report.", {"type": "object", "properties": {"draft_id": {"type": "string"}, "report_id": {"type": "string"}}, "additionalProperties": False}, self._report),
            _Tool("trace.get", "Read an existing audit trace by ID.", {"type": "object", "properties": {"trace_id": {"type": "string"}}, "required": ["trace_id"], "additionalProperties": False}, self._trace),
            _Tool("health.current", "Read the current health summary.", limit_schema, self._health),
        ]

    def _market_bars(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        result = QueryService(self.root, allow_cache_writes=False).execute("bars", symbol=str(args["symbol"]), from_value=str(args["from"]), to_value=str(args["to"]), output_format="telegram")
        return {"ok": result.ok, "rows": [_model_json(row) for row in result.rows], "message": result.message}

    def _news(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        result = QueryService(self.root, allow_cache_writes=False).execute("news", symbol=str(args["symbol"]) if args.get("symbol") else None, limit=int(args.get("limit", 10)), output_format="telegram")
        return {"ok": result.ok, "rows": [_model_json(row) for row in result.rows], "message": result.message}

    def _active_signals(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        repository = SignalRepository(self.connection)
        versions = repository.list_active_versions()
        if args.get("signal_id") is not None:
            versions = [item for item in versions if item.signal_id == args["signal_id"]]
        values: list[dict[str, JsonValue]] = []
        for version in versions:
            item: dict[str, JsonValue] = {"version": version.model_dump(mode="json")}
            if args.get("version") in {None, version.version}:
                item["observations"] = [value.model_dump(mode="json") for value in repository.list_observations(version.signal_id, version.version)]
            values.append(item)
        return {"active_signals": values}

    def _evidence_bundle(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if self.artifact_service is None:
            raise McpServerError("evidence query is not configured")
        identifiers = args["evidence_ids"]
        if not isinstance(identifiers, list) or any(not isinstance(item, str) for item in identifiers):
            raise McpServerError("evidence_ids must be a string array")
        task_id = str(args["task_id"])
        evidence_service = EvidenceService(self.connection, self.artifact_service.store)
        bundle: EvidenceBundle = evidence_service.build_bundle(task_id, [evidence_service.get(task_id, item) for item in identifiers])
        return {"evidence_bundle": bundle.model_dump(mode="json")}

    def _analysis(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        analysis = ReportRepository(self.connection).get_analysis(str(args["analysis_id"]))
        return {"analysis": analysis.model_dump(mode="json") if analysis else None}

    def _report(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        repository = ReportRepository(self.connection)
        if args.get("report_id"):
            report = repository.get_final(str(args["report_id"]))
            return {"final_report": report.model_dump(mode="json") if report else None}
        if args.get("draft_id"):
            draft = repository.get_draft(str(args["draft_id"]))
            return {"report_draft": draft.model_dump(mode="json") if draft else None}
        raise McpServerError("report_id or draft_id is required")

    def _trace(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        row = self.connection.execute("SELECT trace_id, parent_id, module, status, error_msg, created_at FROM trace_chain WHERE trace_id = ?", (str(args["trace_id"]),)).fetchone()
        return {"trace": {key: row[key] for key in row.keys()} if row is not None else None}

    def _health(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
        rows = self.connection.execute("SELECT module, status, heartbeat_at, data_latency_sec, error_rate, consecutive_failures FROM health_metrics ORDER BY timestamp DESC LIMIT ?", (int(args.get("limit", 10)),)).fetchall()
        return {"health": [{key: row[key] for key in row.keys()} for row in rows]}

    def _resource_payload(self, resource: McpResource) -> dict[str, JsonValue]:
        if resource.uri == "stock-agent://version":
            return {"server": "stock-agent", "version": __version__, "protocol": "2025-03-26"}
        if resource.uri == "stock-agent://capabilities":
            return {"tools": [tool["name"] for tool in self.list_tools()], "mode": "read_only_research"}
        return {"tools": self.list_tools(), "resources": [item.model_dump() for item in list_resources()]}

    def _audit(self, name: str, arguments: dict[str, JsonValue], payload: dict[str, JsonValue], *, call_id: str | None, error: Exception | None = None) -> None:
        insert_trace_chain(self.connection, create_trace(trace_id=f"trace-mcp-{call_id or uuid4().hex}", module="mcp_server", input_ref={"tool": name, "argument_keys": sorted(arguments)}, output_ref={"result_keys": sorted(payload)}, status="failed" if error else "success", error_msg=redact_text(str(error)) if error else None, created_at=datetime.now(UTC)))


def serve_stdio(server: StockAgentMcpServer, *, input_stream: TextIO | None = None, output_stream: TextIO | None = None) -> None:
    source, target = input_stream or sys.stdin, output_stream or sys.stdout
    for line in source:
        try:
            request = json.loads(line)
            response = server.handle_request(request if isinstance(request, dict) else {})
        except json.JSONDecodeError:
            response = _jsonrpc_error(None, -32700, "parse error")
        target.write(json.dumps(response, ensure_ascii=False, sort_keys=True) + "\n")
        target.flush()


def _validate_object(arguments: dict[str, JsonValue], schema: dict[str, JsonValue]) -> None:
    required, properties = schema.get("required", []), schema.get("properties", {})
    if not isinstance(required, list) or not isinstance(properties, dict) or any(key not in arguments for key in required):
        raise McpServerError("required tool arguments are missing")
    if schema.get("additionalProperties") is False and set(arguments) - set(properties):
        raise McpServerError("tool arguments contain unsupported fields")


def _model_json(value: object) -> JsonValue:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value  # type: ignore[return-value]


def _safe_payload(value: object) -> dict[str, JsonValue]:
    serialized = json.loads(json.dumps(redact_sensitive(value), ensure_ascii=False, default=str))
    if not isinstance(serialized, dict):
        raise McpServerError("MCP payload must be an object")
    return _redact_paths(serialized)  # type: ignore[return-value]


def _redact_paths(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _redact_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_paths(item) for item in value]
    return _ABSOLUTE_PATH.sub("[redacted-path]", value) if isinstance(value, str) else value


def _jsonrpc_error(request_id: object, code: int, message: str) -> dict[str, JsonValue]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


__all__ = ["McpServerError", "StockAgentMcpServer", "serve_stdio"]

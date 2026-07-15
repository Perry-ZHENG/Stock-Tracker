from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.evidence.service import EvidenceService
from stock_agent.mcp.server import McpServerError, StockAgentMcpServer, serve_stdio
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)


def test_mcp_server_discovers_only_read_only_tools_and_resources(tmp_path: Path) -> None:
    connection, server, _reference = _server(tmp_path)
    initialized = server.initialize()
    tools = server.list_tools()
    resources = server.resources_list()["resources"]

    assert initialized["serverInfo"]["name"] == "stock-agent"
    assert {tool["name"] for tool in tools} >= {"market.bars", "research.evidence_bundle", "signal.active"}
    assert not any(term in tool["name"] for tool in tools for term in ("trade", "order", "approve", "shell", "write", "file"))
    assert {item["uri"] for item in resources} == {
        "stock-agent://capabilities",
        "stock-agent://schemas",
        "stock-agent://version",
    }
    assert "/Users/" not in server.read_resource("stock-agent://schemas")["contents"][0]["text"]
    connection.close()


def test_mcp_server_reads_registered_evidence_and_rejects_mutation(tmp_path: Path) -> None:
    connection, server, reference = _server(tmp_path)
    response = server.call_tool(
        "research.evidence_bundle",
        {"task_id": "task-mcp-server", "evidence_ids": [reference.evidence_id]},
        call_id="call-evidence",
    )
    bundle = response["structuredContent"]["evidence_bundle"]

    assert bundle["evidence_refs"][0]["evidence_id"] == reference.evidence_id
    with pytest.raises(McpServerError):
        server.call_tool("approve.signal", {})
    with pytest.raises(McpServerError):
        server.call_tool("health.current", {"unexpected": "value"})
    connection.close()


def test_mcp_server_enforces_concurrency_and_stdio_jsonrpc(tmp_path: Path) -> None:
    connection, server, _reference = _server(tmp_path, max_concurrency=1)
    assert server._semaphore.acquire(blocking=False)  # exercise the public call boundary under saturation
    with pytest.raises(McpServerError, match="concurrency"):
        server.call_tool("health.current", {})
    server._semaphore.release()

    source = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}) + "\n")
    target = io.StringIO()
    serve_stdio(server, input_stream=source, output_stream=target)
    wire = json.loads(target.getvalue())

    assert wire["id"] == 7
    assert wire["result"]["tools"]
    connection.close()


def _server(tmp_path: Path, *, max_concurrency: int = 4):
    connection = initialize_database(tmp_path / "runtime.sqlite")
    TaskRepository(connection).create_task(
        AgentTask(
            task_id="task-mcp-server",
            request=ResearchRequest(
                request_id="request-mcp-server",
                question="Read verified research outputs.",
                symbols=["QQQ"],
                time_window=TimeWindow(from_ts=NOW - timedelta(days=1), to_ts=NOW, timezone="America/New_York"),
            ),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    artifacts = ArtifactService(ArtifactStore(connection, tmp_path / "lake"))
    artifact = artifacts.save_json(
        "task-mcp-server",
        kind="bars",
        payload={"bars": [{"symbol": "QQQ", "close": 101.0}]},
        source="fixture",
        created_at=NOW,
    )
    reference = EvidenceService(connection, artifacts.store).create(
        "task-mcp-server",
        artifact=artifact,
        evidence_type="bar",
        source="fixture",
        observed_at=NOW,
        evidence_id="evidence-mcp-server",
    )
    return connection, StockAgentMcpServer(root=tmp_path, connection=connection, artifact_service=artifacts, max_concurrency=max_concurrency), reference

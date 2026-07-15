"""CLI handler for the read-only Stock Agent MCP stdio server."""

from __future__ import annotations

from pathlib import Path

from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.config_loader import RuntimeConfigContext
from stock_agent.mcp.server import StockAgentMcpServer, serve_stdio
from stock_agent.storage.sqlite import initialize_runtime_database


def run_mcp_server(root: Path, *, config_context: RuntimeConfigContext) -> int:
    connection = initialize_runtime_database(root, config_context.config)
    try:
        server = StockAgentMcpServer(root=root, connection=connection, artifact_service=ArtifactService(ArtifactStore(connection, root / config_context.config.storage.parquet_root)))
        serve_stdio(server)
    finally:
        connection.close()
    return 0


__all__ = ["run_mcp_server"]

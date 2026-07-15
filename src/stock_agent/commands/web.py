"""FastAPI web-workbench command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.web import create_app

if TYPE_CHECKING:
    from stock_agent.services.agent_service import AgentService


def run_web(
    root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    config_context: RuntimeConfigContext | None = None,
    v2_agent_service: "AgentService | None" = None,
) -> int:
    import uvicorn

    context = config_context or load_config(root)
    uvicorn.run(
        create_app(root, config_context=context, v2_agent_service=v2_agent_service),
        host=host,
        port=port,
        log_level="info",
    )
    return 0


__all__ = ["run_web"]

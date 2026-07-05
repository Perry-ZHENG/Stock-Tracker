"""Build the tool-routing Agent from the configured MODEL API client."""

from __future__ import annotations

from pathlib import Path

from stock_agent.agent.runner import ReactToolAgent
from stock_agent.agent.tools import AgentToolContext, build_default_tool_registry
from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.dialog.langchain_adapter import build_langchain_client


def build_model_agent(
    root: Path,
    *,
    config_context: RuntimeConfigContext | None = None,
) -> ReactToolAgent | None:
    context = config_context or load_config(root)
    model_client = build_langchain_client(context.config.llm)
    if model_client is None:
        return None
    return ReactToolAgent(
        model_client=model_client,
        registry=build_default_tool_registry(),
        context=AgentToolContext(root=root, config_context=context),
    )


__all__ = ["build_model_agent"]

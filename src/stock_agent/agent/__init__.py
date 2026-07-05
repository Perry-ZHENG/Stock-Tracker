"""Model-driven tool-routing agent primitives.

This package is intentionally not connected to production inputs until its
prompt and tool policy have been reviewed.
"""

from stock_agent.agent.prompts import REACT_UI_PROMPT_TEMPLATE, render_react_prompt
from stock_agent.agent.runner import (
    ReactAgentResult,
    ReactToolAgent,
    parse_react_response,
)
from stock_agent.agent.runtime import build_model_agent
from stock_agent.agent.tools import (
    AgentTool,
    AgentToolContext,
    AgentToolRegistry,
    build_default_tool_registry,
)

__all__ = [
    "AgentTool",
    "AgentToolContext",
    "AgentToolRegistry",
    "REACT_UI_PROMPT_TEMPLATE",
    "ReactAgentResult",
    "ReactToolAgent",
    "build_model_agent",
    "build_default_tool_registry",
    "parse_react_response",
    "render_react_prompt",
]

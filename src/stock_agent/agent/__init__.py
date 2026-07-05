"""Model-driven, policy-constrained tool-routing agent primitives."""

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
    FetchTwelveDataBarsArgs,
    build_default_tool_registry,
)

__all__ = [
    "AgentTool",
    "AgentToolContext",
    "AgentToolRegistry",
    "FetchTwelveDataBarsArgs",
    "REACT_UI_PROMPT_TEMPLATE",
    "ReactAgentResult",
    "ReactToolAgent",
    "build_model_agent",
    "build_default_tool_registry",
    "parse_react_response",
    "render_react_prompt",
]

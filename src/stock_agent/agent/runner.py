"""Model-agnostic ReAct loop for selecting and invoking registered tools."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import ValidationError

from stock_agent.agent.prompts import render_react_prompt
from stock_agent.agent.tools import AgentToolContext, AgentToolRegistry

ModelClient = Callable[[str], str]
AgentRunStatus = Literal[
    "succeeded",
    "needs_user_input",
    "no_suitable_tool",
    "failed",
]


class ReactResponseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedAction:
    action_type: Literal["tool", "finish"]
    name: str
    arguments: dict[str, Any] | None = None
    final_answer: str | None = None


@dataclass(frozen=True)
class AgentToolCall:
    tool_name: str
    arguments: dict[str, Any]
    observation: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "observation": self.observation,
        }


@dataclass(frozen=True)
class ReactAgentResult:
    status: AgentRunStatus
    output: str
    selected_tool: str | None
    tool_calls: list[AgentToolCall] = field(default_factory=list)
    model_steps: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "output": self.output,
            "selected_tool": self.selected_tool,
            "tool_calls": [call.as_dict() for call in self.tool_calls],
            "model_steps": self.model_steps,
        }


class ReactToolAgent:
    """Ask a model to select tools, execute them, and return a final answer."""

    def __init__(
        self,
        *,
        model_client: ModelClient,
        registry: AgentToolRegistry,
        context: AgentToolContext,
        max_steps: int = 4,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.model_client = model_client
        self.registry = registry
        self.context = context
        self.max_steps = max_steps

    def run(self, question: str, *, history: str = "") -> ReactAgentResult:
        observations: list[dict[str, Any]] = []
        tool_calls: list[AgentToolCall] = []
        selected_tool: str | None = None

        for step in range(1, self.max_steps + 1):
            prompt = render_react_prompt(
                tools=self.registry.prompt_text(),
                question=question,
                history=history,
                observation=_render_observations(observations),
            )
            try:
                raw_response = self.model_client(prompt)
            except Exception as exc:  # pragma: no cover - external MODEL API boundary
                return ReactAgentResult(
                    status="failed",
                    output=_model_error_message(exc),
                    selected_tool=selected_tool,
                    tool_calls=tool_calls,
                    model_steps=step,
                )
            try:
                action = parse_react_response(raw_response)
            except ReactResponseError as exc:
                observations.append(
                    {
                        "ok": False,
                        "status": "invalid_model_response",
                        "message": str(exc),
                    }
                )
                continue

            if action.action_type == "finish":
                return ReactAgentResult(
                    status="succeeded",
                    output=action.final_answer or "",
                    selected_tool=selected_tool,
                    tool_calls=tool_calls,
                    model_steps=step,
                )

            selected_tool = action.name
            arguments = action.arguments or {}
            tool = self.registry.get(action.name)
            if tool is None:
                observation = {
                    "ok": False,
                    "status": "no_suitable_tool",
                    "message": f"模型请求了未注册工具: {action.name}",
                }
            else:
                try:
                    observation = tool.invoke(self.context, arguments)
                except ValidationError as exc:
                    observation = {
                        "ok": False,
                        "status": "invalid_tool_arguments",
                        "message": "工具参数未通过校验",
                        "errors": exc.errors(include_url=False),
                    }
                except Exception as exc:  # pragma: no cover - external boundary
                    observation = {
                        "ok": False,
                        "status": "tool_failed",
                        "message": f"{exc.__class__.__name__}: {exc}",
                    }

            call = AgentToolCall(
                tool_name=action.name,
                arguments=arguments,
                observation=observation,
            )
            tool_calls.append(call)
            observations.append(call.as_dict())

            status = observation.get("status")
            if action.name == "ask_user" or status == "needs_user_input":
                return ReactAgentResult(
                    status="needs_user_input",
                    output=str(observation.get("question") or observation.get("message") or ""),
                    selected_tool=action.name,
                    tool_calls=tool_calls,
                    model_steps=step,
                )
            if action.name == "no_suitable_tool" or status == "no_suitable_tool":
                return ReactAgentResult(
                    status="no_suitable_tool",
                    output=str(observation.get("message") or ""),
                    selected_tool=action.name,
                    tool_calls=tool_calls,
                    model_steps=step,
                )
            if observation.get("ok") is True:
                return ReactAgentResult(
                    status="succeeded",
                    output=_successful_tool_output(action.name, observation),
                    selected_tool=action.name,
                    tool_calls=tool_calls,
                    model_steps=step,
                )

        return ReactAgentResult(
            status="failed",
            output="Agent 在最大步骤数内没有得到可执行工具或最终答案。",
            selected_tool=selected_tool,
            tool_calls=tool_calls,
            model_steps=self.max_steps,
        )


def parse_react_response(response: str) -> ParsedAction:
    text = response.strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    match = re.search(r"(?ms)^Action:\s*(.+?)\s*$", text)
    if match is None:
        raise ReactResponseError("模型响应缺少 Action 字段")
    action_text = match.group(1).strip()
    if action_text.startswith("Finish[") and action_text.endswith("]"):
        return ParsedAction(
            action_type="finish",
            name="Finish",
            final_answer=action_text[len("Finish[") : -1].strip(),
        )
    bracket = action_text.find("[")
    if bracket <= 0 or not action_text.endswith("]"):
        raise ReactResponseError("Action 必须使用 tool_name[JSON] 或 Finish[答案]")
    name = action_text[:bracket].strip()
    payload_text = action_text[bracket + 1 : -1].strip()
    try:
        arguments = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ReactResponseError("工具参数不是合法 JSON") from exc
    if not isinstance(arguments, dict):
        raise ReactResponseError("工具参数必须是 JSON 对象")
    return ParsedAction(
        action_type="tool",
        name=name,
        arguments=arguments,
    )


def _render_observations(observations: list[dict[str, Any]]) -> str:
    if not observations:
        return "无"
    return json.dumps(observations, ensure_ascii=False, sort_keys=True)


def _model_error_message(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    message = str(exc).lower()
    if status_code == 429 or "429" in message or "rate limit" in message:
        return "MODEL API 当前触发免费额度限流，请稍后重试。"
    return f"MODEL API 暂时不可用：{exc.__class__.__name__}"


def _successful_tool_output(tool_name: str, observation: dict[str, Any]) -> str:
    text = observation.get("text")
    if isinstance(text, str) and text.strip():
        return text
    rows = observation.get("rows")
    count = observation.get("count")
    if isinstance(rows, list) and rows:
        return json.dumps(
            {
                "tool": tool_name,
                "count": count if isinstance(count, int) else len(rows),
                "rows": rows,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    if count == 0:
        return f"{tool_name} 执行成功，未找到匹配结果。"
    return f"{tool_name} 执行成功。"


__all__ = [
    "AgentRunStatus",
    "AgentToolCall",
    "ModelClient",
    "ParsedAction",
    "ReactAgentResult",
    "ReactResponseError",
    "ReactToolAgent",
    "parse_react_response",
]

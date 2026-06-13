"""Optional LLM parser that can only emit validated command intents."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from stock_agent.dialog.intents import CommandIntent, validate_intent
from stock_agent.dialog.parser import parse_structured_command
from stock_agent.dialog.safety import blocked_intent_for_text, clarification, enforce_llm_permissions

LlmClient = Callable[[str], str]


@dataclass(frozen=True)
class LlmParser:
    client: LlmClient | None = None
    enabled: bool = False

    def parse(self, text: str) -> CommandIntent:
        blocked = blocked_intent_for_text(text)
        if blocked is not None:
            return blocked

        if not self.enabled or self.client is None:
            return parse_structured_command(text, source="llm")

        try:
            payload = json.loads(self.client(_prompt(text)))
        except json.JSONDecodeError:
            return clarification(text, "LLM 输出不是合法 JSON，请换一种更明确的说法。")

        if not isinstance(payload, dict):
            return clarification(text, "LLM 输出必须是单个 command intent 对象。")

        payload["source"] = "llm"
        payload.setdefault("raw_text", text)
        try:
            intent = validate_intent(payload)
        except ValidationError:
            return clarification(text, "LLM 输出未通过 command intent schema 校验。")
        return enforce_llm_permissions(intent, raw_text=text)


def _prompt(text: str) -> str:
    return (
        "Convert the user text into one JSON CommandIntent. "
        "Do not include explanations, calculations, database results, or trading advice. "
        f"User text: {text}"
    )


__all__ = ["LlmClient", "LlmParser"]

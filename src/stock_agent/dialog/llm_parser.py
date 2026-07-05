"""Optional LLM parser that can only emit validated command intents."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from stock_agent.dialog.intents import CommandIntent, validate_intent
from stock_agent.dialog.natural_language import parse_natural_language_command
from stock_agent.dialog.parser import parse_structured_command
from stock_agent.dialog.safety import blocked_intent_for_text, clarification, enforce_llm_permissions
from stock_agent.dialog.time_window import explicit_market_time_question

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
            structured = parse_structured_command(text, source="llm")
            natural = parse_natural_language_command(text)
            return natural or structured

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
            if payload.get("query") == "bars" or (
                payload.get("query") == "signals" and payload.get("symbol")
            ):
                return clarification(
                    text,
                    explicit_market_time_question(str(payload.get("symbol") or "") or None),
                )
            return clarification(text, "LLM 输出未通过 command intent schema 校验。")
        return enforce_llm_permissions(intent, raw_text=text)


def _prompt(text: str) -> str:
    return (
        "Convert the user text into one JSON CommandIntent. "
        "Do not include explanations, calculations, database results, or trading advice. "
        "For bars, or signals with a specific symbol, require from_ts, to_ts, and an explicit "
        "IANA timezone. Both timestamps must contain a date and clock time. If any part is "
        "missing or relative, return a clarification intent and ask for the complete time range. "
        f"User text: {text}"
    )


__all__ = ["LlmClient", "LlmParser"]

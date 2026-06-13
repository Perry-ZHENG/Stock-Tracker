"""Safety gates for dialog and LLM-produced intents."""

from __future__ import annotations

import re
from typing import Iterable

from stock_agent.dialog.intents import (
    ClarificationIntent,
    CommandIntent,
    HighRiskBlockedIntent,
    LocalAdminIntent,
    PendingChangeIntent,
    ReadOnlyIntent,
    validate_intent,
)

HIGH_RISK_TEXT_MARKERS = (
    "确定买入",
    "保证收益",
    "替我下单",
    "下单",
    "撤单",
    "转账",
    "提现",
    "改密码",
    "buy ",
    "sell ",
    "place order",
    "cancel order",
    "withdraw",
    "transfer",
)


def blocked_intent_for_text(text: str) -> HighRiskBlockedIntent | None:
    normalized = f" {text.lower()} "
    if not any(marker.lower() in normalized for marker in HIGH_RISK_TEXT_MARKERS):
        return None
    return validate_intent(
        {
            "intent_type": "high_risk_blocked",
            "source": "llm",
            "raw_text": text,
            "requested_action": "unknown_high_risk",
            "blocked_reason": "natural language request contains trading, money movement, or guaranteed-return language",
        }
    )


def enforce_llm_permissions(intent: CommandIntent, *, raw_text: str) -> CommandIntent:
    """Allow only safe intent classes from LLM output."""

    if isinstance(intent, HighRiskBlockedIntent):
        return intent
    if isinstance(intent, LocalAdminIntent):
        return clarification(raw_text, "LLM 不能执行本地管理命令，请使用 CLI 明确命令。")
    if isinstance(intent, ReadOnlyIntent) and _has_ambiguous_symbol([intent.symbol, *intent.symbols]):
        return clarification(raw_text, "无法确认具体股票代码，请给出明确 ticker，例如 QQQ。")
    if isinstance(intent, PendingChangeIntent) and _has_ambiguous_symbol([intent.symbol]):
        return clarification(raw_text, "配置变更需要明确 ticker 或策略名称。")
    return intent


def clarification(raw_text: str, question: str) -> ClarificationIntent:
    return validate_intent(
        {
            "intent_type": "clarification",
            "source": "llm",
            "raw_text": raw_text,
            "question": question,
            "candidates": ["show signals QQQ", "add symbol QQQ", "trace SIGNAL_ID"],
        }
    )


def _has_ambiguous_symbol(symbols: Iterable[str | None]) -> bool:
    for symbol in symbols:
        if symbol is None:
            continue
        if not re.fullmatch(r"[A-Z]{1,6}([.\-][A-Z])?", symbol.upper()):
            return True
    return False


__all__ = ["HIGH_RISK_TEXT_MARKERS", "blocked_intent_for_text", "clarification", "enforce_llm_permissions"]

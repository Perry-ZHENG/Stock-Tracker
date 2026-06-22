"""Deterministic structured command parser."""

from __future__ import annotations

import re
from dataclasses import dataclass

from stock_agent.dialog.intents import (
    BlockedAction,
    ClarificationIntent,
    CommandIntent,
    IntentSource,
    PendingChangeAction,
    validate_intent,
)

EXAMPLES = [
    "show signals NVDA limit 5",
    "show health",
    "bars QQQ from 2026-05-22 to 2026-05-26",
    "trace sig-001",
    "add symbol QQQ",
    "enable strategy macd",
]

_HIGH_RISK_PATTERNS: list[tuple[BlockedAction, tuple[str, ...]]] = [
    ("place_order", ("buy ", "sell ", "place order", "下单", "买入", "卖出")),
    ("modify_order", ("modify order", "改单", "修改订单")),
    ("cancel_order", ("cancel order", "撤单", "取消订单")),
    ("transfer_funds", ("transfer", "转账")),
    ("withdraw_funds", ("withdraw", "出金", "提现")),
    (
        "read_secret",
        (
            "api key",
            "api-key",
            "api_key",
            "apikey",
            "openai_api_key",
            "telegram_bot_token",
            "env var",
            "environment variable",
            "token",
            "secret",
            "credential",
            "密钥",
            "令牌",
            "凭证",
            "环境变量",
            "模型使用的 api",
        ),
    ),
    ("change_password", ("password", "改密码", "修改密码")),
    ("change_account", ("account", "账户变更", "改账户")),
]


@dataclass(frozen=True)
class ParseFailure:
    raw_text: str
    message: str
    examples: list[str]

    def to_intent(self, *, source: IntentSource) -> ClarificationIntent:
        return validate_intent(
            {
                "intent_type": "clarification",
                "source": source,
                "raw_text": self.raw_text,
                "question": self.message,
                "candidates": self.examples,
            }
        )


def parse_structured_command(text: str, *, source: IntentSource = "structured_parser") -> CommandIntent:
    """Parse a deterministic CLI/Telegram command into a safe intent schema."""

    normalized = _normalize(text)
    if not normalized:
        return _clarify(text, "请输入要执行的查询或配置变更命令。", source=source)

    high_risk = _high_risk_intent(normalized, raw_text=text, source=source)
    if high_risk is not None:
        return high_risk

    for parser in (
        _parse_pending_change,
        _parse_bars,
        _parse_trace,
        _parse_stats,
        _parse_news,
        _parse_health,
        _parse_provider_compare,
        _parse_abnormal_bars,
        _parse_schedule,
        _parse_signals,
    ):
        payload = parser(normalized)
        if payload is not None:
            payload["source"] = source
            payload["raw_text"] = text
            return validate_intent(payload)

    return _clarify(text, "无法确定你的意图，请选择一个支持的结构化命令。", source=source)


def _parse_pending_change(text: str) -> dict[str, object] | None:
    chinese_add = re.fullmatch(r"添加\s+([a-zA-Z][a-zA-Z0-9.\-]*)\s+到关注", text)
    if chinese_add:
        return {
            "intent_type": "pending_change",
            "action": "add_symbol",
            "symbol": chinese_add.group(1),
        }

    match = re.fullmatch(r"(add|remove)\s+symbol\s+([a-zA-Z][a-zA-Z0-9.\-]*)", text)
    if match:
        verb, symbol = match.groups()
        return {
            "intent_type": "pending_change",
            "action": "add_symbol" if verb == "add" else "remove_symbol",
            "symbol": symbol,
        }

    match = re.fullmatch(r"(enable|disable)\s+strategy\s+([a-zA-Z][a-zA-Z0-9_\-]*)", text)
    if match:
        verb, strategy_id = match.groups()
        return {
            "intent_type": "pending_change",
            "action": "enable_strategy" if verb == "enable" else "disable_strategy",
            "strategy_id": strategy_id,
        }

    match = re.fullmatch(r"change\s+watch\s+window\s+([0-2]?\d:[0-5]\d)\s+([0-2]?\d:[0-5]\d)", text)
    if match:
        start, end = match.groups()
        return {
            "intent_type": "pending_change",
            "action": "change_watch_window",
            "watch_window": {"regular_session_start": start, "regular_session_end": end},
        }
    return None


def _parse_signals(text: str) -> dict[str, object] | None:
    chinese_match = re.fullmatch(r"最近\s+([a-zA-Z][a-zA-Z0-9.\-]*)\s+有什么信号", text)
    if chinese_match:
        return {"intent_type": "read_only", "query": "signals", "symbol": chinese_match.group(1), "limit": 10}

    match = re.fullmatch(r"(?:show\s+)?signals(?:\s+([a-zA-Z][a-zA-Z0-9.\-]*))?(?:\s+limit\s+(\d+))?", text)
    if not match:
        return None
    symbol, limit = match.groups()
    return {"intent_type": "read_only", "query": "signals", "symbol": symbol, "limit": int(limit or 10)}


def _parse_health(text: str) -> dict[str, object] | None:
    if text in {"health", "show health"}:
        return {"intent_type": "read_only", "query": "health"}
    return None


def _parse_news(text: str) -> dict[str, object] | None:
    match = re.fullmatch(r"(?:show\s+)?news(?:\s+([a-zA-Z][a-zA-Z0-9.\-]*))?(?:\s+limit\s+(\d+))?", text)
    if not match:
        return None
    symbol, limit = match.groups()
    return {"intent_type": "read_only", "query": "news", "symbol": symbol, "limit": int(limit or 10)}


def _parse_stats(text: str) -> dict[str, object] | None:
    match = re.fullmatch(r"(?:show\s+)?stats(?:\s+(day|month|year))?", text)
    if not match:
        return None
    period = match.group(1) or "day"
    return {"intent_type": "read_only", "query": "stats", "period": period}


def _parse_schedule(text: str) -> dict[str, object] | None:
    if text in {"schedule", "show schedule"}:
        return {"intent_type": "read_only", "query": "schedule"}
    return None


def _parse_provider_compare(text: str) -> dict[str, object] | None:
    if text in {"provider compare", "provider-compare", "show provider compare", "show provider-compare"}:
        return {"intent_type": "read_only", "query": "provider-compare"}
    return None


def _parse_abnormal_bars(text: str) -> dict[str, object] | None:
    if text in {"abnormal bars", "abnormal-bars", "show abnormal bars", "show abnormal-bars"}:
        return {"intent_type": "read_only", "query": "abnormal-bars"}
    return None


def _parse_trace(text: str) -> dict[str, object] | None:
    match = re.fullmatch(r"(?:show\s+)?trace\s+([a-zA-Z0-9_.:\-]+)", text)
    if not match:
        return None
    return {"intent_type": "read_only", "query": "trace", "target_id": match.group(1)}


def _parse_bars(text: str) -> dict[str, object] | None:
    match = re.fullmatch(
        r"(?:show\s+)?bars\s+([a-zA-Z][a-zA-Z0-9.\-]*)(?:\s+from\s+([^\s]+))?(?:\s+to\s+([^\s]+))?",
        text,
    )
    if not match:
        return None
    symbol, from_ts, to_ts = match.groups()
    return {
        "intent_type": "read_only",
        "query": "bars",
        "symbol": symbol,
        "from_ts": from_ts,
        "to_ts": to_ts,
    }


def _high_risk_intent(text: str, *, raw_text: str, source: IntentSource) -> CommandIntent | None:
    padded = f" {text} "
    for action, tokens in _HIGH_RISK_PATTERNS:
        if any(token in padded for token in tokens):
            return validate_intent(
                {
                    "intent_type": "high_risk_blocked",
                    "source": source,
                    "raw_text": raw_text,
                    "requested_action": action,
                    "blocked_reason": "structured parser blocks trading, account, credential, and money movement actions",
                }
            )
    return None


def _clarify(text: str, message: str, *, source: IntentSource) -> ClarificationIntent:
    return ParseFailure(raw_text=text, message=message, examples=EXAMPLES).to_intent(source=source)


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


__all__ = ["EXAMPLES", "ParseFailure", "parse_structured_command"]

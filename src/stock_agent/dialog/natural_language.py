"""Lightweight natural-language field extraction for local CLI interaction."""

from __future__ import annotations

import re
from collections.abc import Iterable

from stock_agent.dialog.intents import CommandIntent, validate_intent
from stock_agent.dialog.time_window import (
    explicit_market_time_question,
    extract_explicit_time_window,
)

_SYMBOL_RE = re.compile(r"\b[A-Za-z]{1,6}(?:[.-][A-Za-z])?\b")
_ID_RE = re.compile(r"\b(?:sig|trace|chg)[A-Za-z0-9_.:-]*\b", re.IGNORECASE)
_TIME_RE = re.compile(r"\b([0-2]?\d:[0-5]\d)\b")
_LIMIT_RE = re.compile(r"\b(?:limit|top|last|latest|recent)\s+(\d{1,3})\b", re.IGNORECASE)
_ZH_LIMIT_RE = re.compile(r"(\d{1,3})\s*(?:条|個|个|筆|笔)")

_STOPWORDS = {
    "add",
    "agent",
    "and",
    "any",
    "bar",
    "bars",
    "can",
    "change",
    "check",
    "disable",
    "enable",
    "explain",
    "follow",
    "for",
    "health",
    "latest",
    "limit",
    "me",
    "month",
    "news",
    "please",
    "recent",
    "remove",
    "schedule",
    "show",
    "signal",
    "signals",
    "stats",
    "status",
    "strategy",
    "the",
    "trace",
    "watch",
    "watchlist",
    "window",
    "year",
}

_STRATEGIES = {"ma_cross", "macd", "kdj", "boll", "active_j"}
_SECRET_MARKERS = (
    "api key",
    "api-key",
    "api_key",
    "apikey",
    "openai_api_key",
    "openrouter_api_key",
    "telegram_bot_token",
    "market_data_api_key",
    "news_api_key",
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
)


def parse_natural_language_command(text: str) -> CommandIntent | None:
    """Extract key fields from natural language and return a safe intent.

    This parser is intentionally conservative. It only returns an intent when
    it can lock the fields needed to build an equivalent controlled command.
    """

    raw_text = text.strip()
    normalized = _normalize(raw_text)
    if not normalized:
        return None

    if _looks_like_secret_request(raw_text):
        return validate_intent(
            {
                "intent_type": "high_risk_blocked",
                "source": "cli",
                "raw_text": raw_text,
                "requested_action": "read_secret",
                "blocked_reason": "natural language request asks for API keys, tokens, credentials, or environment secrets",
            }
        )

    if _contains_any(normalized, "cancel order", "place order", "withdraw", "transfer") or _contains_any(
        raw_text, "下单", "买入", "卖出", "撤单", "转账", "提现吗", "提现"
    ):
        return validate_intent(
            {
                "intent_type": "high_risk_blocked",
                "source": "cli",
                "raw_text": raw_text,
                "requested_action": "unknown_high_risk",
                "blocked_reason": "natural language request contains trading, money movement, or credential access language",
            }
        )

    symbol = _extract_symbol(raw_text)
    limit = _extract_limit(raw_text)
    market_time_window = extract_explicit_time_window(raw_text)

    if _contains_any(normalized, "remove", "unfollow", "stop watching") or _contains_any(raw_text, "取消关注", "移除关注", "删除关注"):
        if symbol:
            return validate_intent(
                {
                    "intent_type": "pending_change",
                    "source": "cli",
                    "raw_text": raw_text,
                    "action": "remove_symbol",
                    "symbol": symbol,
                    "reason": "natural language field extraction",
                }
            )

    if _contains_any(normalized, "add", "follow", "watch", "track") or _contains_any(raw_text, "加入关注", "添加关注", "关注"):
        if symbol:
            return validate_intent(
                {
                    "intent_type": "pending_change",
                    "source": "cli",
                    "raw_text": raw_text,
                    "action": "add_symbol",
                    "symbol": symbol,
                    "reason": "natural language field extraction",
                }
            )

    strategy_id = _extract_strategy(normalized)
    if strategy_id and (_contains_any(normalized, "enable", "turn on") or _contains_any(raw_text, "启用", "打开")):
        return validate_intent(
            {
                "intent_type": "pending_change",
                "source": "cli",
                "raw_text": raw_text,
                "action": "enable_strategy",
                "strategy_id": strategy_id,
                "reason": "natural language field extraction",
            }
        )
    if strategy_id and (_contains_any(normalized, "disable", "turn off") or _contains_any(raw_text, "禁用", "关闭")):
        return validate_intent(
            {
                "intent_type": "pending_change",
                "source": "cli",
                "raw_text": raw_text,
                "action": "disable_strategy",
                "strategy_id": strategy_id,
                "reason": "natural language field extraction",
            }
        )

    if _contains_any(normalized, "watch window", "market window") or _contains_any(raw_text, "盯盘窗口", "交易窗口"):
        times = _TIME_RE.findall(raw_text)
        if len(times) >= 2:
            return validate_intent(
                {
                    "intent_type": "pending_change",
                    "source": "cli",
                    "raw_text": raw_text,
                    "action": "change_watch_window",
                    "watch_window": {"regular_session_start": times[0], "regular_session_end": times[1]},
                    "reason": "natural language field extraction",
                }
            )

    trace_id = _extract_target_id(raw_text)
    if trace_id and (_contains_any(normalized, "trace", "explain", "why", "source") or _contains_any(raw_text, "解释", "追踪", "来源", "为什么")):
        return validate_intent(
            {
                "intent_type": "read_only",
                "source": "cli",
                "raw_text": raw_text,
                "query": "trace",
                "target_id": trace_id,
            }
        )

    if _contains_any(normalized, "health", "status") or _contains_any(raw_text, "健康", "状态"):
        return validate_intent({"intent_type": "read_only", "source": "cli", "raw_text": raw_text, "query": "health"})

    if _contains_any(normalized, "schedule", "market hours", "calendar") or _contains_any(raw_text, "日程", "交易时间", "开盘"):
        return validate_intent({"intent_type": "read_only", "source": "cli", "raw_text": raw_text, "query": "schedule"})

    if _contains_any(normalized, "provider compare", "provider comparison") or _contains_any(raw_text, "数据源对比"):
        return validate_intent({"intent_type": "read_only", "source": "cli", "raw_text": raw_text, "query": "provider-compare"})

    if _contains_any(normalized, "abnormal bars", "bad bars") or _contains_any(raw_text, "异常bar", "异常 bar"):
        return validate_intent({"intent_type": "read_only", "source": "cli", "raw_text": raw_text, "query": "abnormal-bars"})

    if _contains_any(normalized, "news") or _contains_any(raw_text, "新闻", "资讯"):
        return validate_intent(
            {
                "intent_type": "read_only",
                "source": "cli",
                "raw_text": raw_text,
                "query": "news",
                "symbol": symbol,
                "limit": limit,
            }
        )

    if _contains_any(normalized, "stats", "statistics") or _contains_any(raw_text, "统计"):
        return validate_intent(
            {
                "intent_type": "read_only",
                "source": "cli",
                "raw_text": raw_text,
                "query": "stats",
                "period": _extract_period(normalized),
            }
        )

    if _contains_any(normalized, "bars", "ohlcv") or _contains_any(raw_text, "行情", "K线", "bar"):
        if symbol:
            if market_time_window is None:
                return _market_time_clarification(raw_text, symbol)
            return validate_intent(
                {
                    "intent_type": "read_only",
                    "source": "cli",
                    "raw_text": raw_text,
                    "query": "bars",
                    "symbol": symbol,
                    **market_time_window,
                }
            )

    if _contains_any(normalized, "signal", "signals", "alert", "alerts") or _contains_any(raw_text, "信号", "提醒"):
        if symbol and market_time_window is None:
            return _market_time_clarification(raw_text, symbol)
        return validate_intent(
            {
                "intent_type": "read_only",
                "source": "cli",
                "raw_text": raw_text,
                "query": "signals",
                "symbol": symbol,
                "limit": limit,
                **(market_time_window or {}),
            }
        )

    return None


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _contains_any(text: str, *markers: str) -> bool:
    return any(marker.lower() in text.lower() for marker in markers)


def _looks_like_secret_request(text: str) -> bool:
    return _contains_any(text, *_SECRET_MARKERS)


def _extract_symbol(text: str) -> str | None:
    for token in _SYMBOL_RE.findall(text):
        if token.lower() in _STOPWORDS:
            continue
        if token.lower() in _STRATEGIES:
            continue
        if token.lower().startswith(("sig", "trace", "chg")):
            continue
        return token.upper()
    return None


def _extract_limit(text: str) -> int:
    for pattern in (_LIMIT_RE, _ZH_LIMIT_RE):
        match = pattern.search(text)
        if match:
            return max(1, min(int(match.group(1)), 100))
    return 10


def _extract_strategy(text: str) -> str | None:
    normalized = text.lower().replace("-", "_")
    for strategy in _STRATEGIES:
        if strategy in normalized:
            return strategy
    if "bollinger" in normalized:
        return "boll"
    return None


def _extract_period(text: str) -> str:
    if "year" in text or "annual" in text or "年" in text:
        return "year"
    if "month" in text or "月" in text:
        return "month"
    return "day"


def _extract_target_id(text: str) -> str | None:
    match = _ID_RE.search(text)
    return match.group(0) if match else None


def _market_time_clarification(raw_text: str, symbol: str) -> CommandIntent:
    return validate_intent(
        {
            "intent_type": "clarification",
            "source": "cli",
            "raw_text": raw_text,
            "question": explicit_market_time_question(symbol),
            "candidates": [
                (
                    f"查询 {symbol} 从 2026-07-06 09:30 到 "
                    "2026-07-06 16:00 的信号，America/New_York"
                )
            ],
        }
    )


__all__ = ["parse_natural_language_command"]

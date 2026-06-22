"""Interactive planning layer for natural-language CLI commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from stock_agent.dialog.intents import (
    ClarificationIntent,
    CommandIntent,
    HighRiskBlockedIntent,
    LocalAdminIntent,
    PendingChangeIntent,
    ReadOnlyIntent,
)
from stock_agent.dialog.llm_parser import LlmParser
from stock_agent.dialog.natural_language import parse_natural_language_command
from stock_agent.dialog.parser import parse_structured_command

ChatClient = Callable[[str], str]


@dataclass(frozen=True)
class InteractionPlan:
    raw_text: str
    intent: CommandIntent | None
    parser_name: str
    requires_confirmation: bool
    command_preview: str | None = None
    fields: dict[str, object] | None = None
    chat_response: str | None = None

    @property
    def is_chat(self) -> bool:
        return self.chat_response is not None


def build_interaction_plan(
    text: str,
    *,
    llm_parser: LlmParser | None = None,
    chat_client: ChatClient | None = None,
) -> InteractionPlan:
    """Build a safe plan before an interactive CLI command is executed."""

    structured_intent = parse_structured_command(text, source="cli")
    if not isinstance(structured_intent, ClarificationIntent):
        return _command_plan(text, structured_intent, parser_name="structured", requires_confirmation=False)

    natural_intent = parse_natural_language_command(text)
    if natural_intent is not None:
        return _command_plan(text, natural_intent, parser_name="natural_fields", requires_confirmation=True)

    if llm_parser is not None and llm_parser.enabled:
        llm_intent = llm_parser.parse(text)
        if not isinstance(llm_intent, ClarificationIntent):
            return _command_plan(text, llm_intent, parser_name="llm", requires_confirmation=True)

    if chat_client is not None:
        return InteractionPlan(
            raw_text=text,
            intent=None,
            parser_name="langchain_chat",
            requires_confirmation=False,
            chat_response=chat_client(_chat_prompt(text)),
        )

    local_response = _local_chat_response(text)
    if local_response is not None:
        return InteractionPlan(
            raw_text=text,
            intent=None,
            parser_name="local_chat",
            requires_confirmation=False,
            chat_response=local_response,
        )

    return _command_plan(text, structured_intent, parser_name="clarification", requires_confirmation=False)


def format_interaction_plan(plan: InteractionPlan) -> str:
    if plan.is_chat:
        return f"assistant_response=true\n{plan.chat_response}\n"
    lines = [
        "intent_preview=true",
        f"parser={plan.parser_name}",
    ]
    if plan.intent is not None:
        lines.append(f"intent_type={plan.intent.intent_type}")
        lines.append(f"risk={plan.intent.risk}")
    if plan.command_preview:
        lines.append(f"command_preview={plan.command_preview}")
    if plan.fields:
        lines.append("fields:")
        for key, value in plan.fields.items():
            lines.append(f"{key}={value}")
    lines.append(f"confirmation_required={str(plan.requires_confirmation).lower()}")
    return "\n".join(lines) + "\n"


def _command_plan(
    text: str,
    intent: CommandIntent,
    *,
    parser_name: str,
    requires_confirmation: bool,
) -> InteractionPlan:
    if isinstance(intent, (HighRiskBlockedIntent, ClarificationIntent, LocalAdminIntent)):
        requires_confirmation = False
    return InteractionPlan(
        raw_text=text,
        intent=intent,
        parser_name=parser_name,
        requires_confirmation=requires_confirmation,
        command_preview=_command_preview(intent),
        fields=_intent_fields(intent),
    )


def _command_preview(intent: CommandIntent) -> str | None:
    if isinstance(intent, ReadOnlyIntent):
        parts = ["stock-agent", "cli", intent.query]
        if intent.target_id:
            parts.append(intent.target_id)
        if intent.symbol:
            parts.extend(["--symbol", intent.symbol])
        if intent.limit != 10:
            parts.extend(["--limit", str(intent.limit)])
        if intent.period:
            parts.extend(["--period", intent.period])
        if intent.from_ts:
            parts.extend(["--from", intent.from_ts])
        if intent.to_ts:
            parts.extend(["--to", intent.to_ts])
        return " ".join(parts)
    if isinstance(intent, PendingChangeIntent):
        if intent.symbol:
            return f"stock-agent cli {intent.action.replace('_', '-')} {intent.symbol}"
        if intent.strategy_id:
            return f"stock-agent cli {intent.action.replace('_', '-')} {intent.strategy_id}"
        if intent.watch_window:
            return f"stock-agent cli {intent.action.replace('_', '-')} {intent.watch_window}"
        return f"stock-agent cli {intent.action.replace('_', '-')}"
    if isinstance(intent, HighRiskBlockedIntent):
        return f"blocked:{intent.requested_action}"
    return None


def _intent_fields(intent: CommandIntent) -> dict[str, object]:
    if isinstance(intent, ReadOnlyIntent):
        fields: dict[str, object] = {"query": intent.query, "limit": intent.limit}
        if intent.symbol:
            fields["symbol"] = intent.symbol
        if intent.target_id:
            fields["target_id"] = intent.target_id
        if intent.period:
            fields["period"] = intent.period
        if intent.from_ts:
            fields["from_ts"] = intent.from_ts
        if intent.to_ts:
            fields["to_ts"] = intent.to_ts
        return fields
    if isinstance(intent, PendingChangeIntent):
        fields = {"action": intent.action}
        if intent.symbol:
            fields["symbol"] = intent.symbol
        if intent.strategy_id:
            fields["strategy_id"] = intent.strategy_id
        if intent.watch_window:
            fields["watch_window"] = intent.watch_window
        return fields
    if isinstance(intent, HighRiskBlockedIntent):
        return {"requested_action": intent.requested_action, "blocked_reason": intent.blocked_reason}
    if isinstance(intent, ClarificationIntent):
        return {"question": intent.question, "candidates": intent.candidates}
    if isinstance(intent, LocalAdminIntent):
        return {"action": intent.action, "dry_run": intent.dry_run}
    return {}


def _chat_prompt(text: str) -> str:
    return (
        "You are Stock Agent, a local-first market-watch assistant. "
        "Answer conversationally and briefly. Do not provide financial advice, "
        "do not claim guaranteed returns, and do not execute commands. "
        f"User text: {text}"
    )


def _local_chat_response(text: str) -> str | None:
    normalized = text.lower()
    if _contains_any(text, "你能做什么", "可以做什么", "怎么用", "帮助", "有哪些命令") or _contains_any(
        normalized, "help", "commands", "what can you do", "how to use"
    ):
        return (
            "我可以作为 Stock Agent 的本地 CLI 助手使用。常用能力包括：\n"
            "- 查询信号：例如 `show me latest QQQ signals`\n"
            "- 查看健康状态：例如 `is the system healthy` 或 `show health`\n"
            "- 追踪信号原因：例如 `explain sig-001`\n"
            "- 查询行情 bar：例如 `show bars QQQ`\n"
            "- 查看日程：例如 `show schedule`\n"
            "- 提交配置变更到 pending_review：例如 `please add QQQ to my watchlist`\n"
            "自然语言命令会先展示 command_preview 和 fields，只有你输入 yes 后才会执行。"
        )
    if _contains_any(text, "天气", "菜谱", "电影", "音乐", "笑话") or _contains_any(
        normalized, "weather", "recipe", "movie", "music", "joke"
    ):
        return (
            "这个 CLI 主要服务于 Stock Agent 项目本身，不能查询天气或处理通用生活问题。"
            "你可以问我股票信号、健康状态、trace、bars、schedule，或者让我把明确的配置变更转成待审核命令。"
        )
    if text.endswith("?") or text.endswith("？"):
        return (
            "我没有把这句话识别成可执行的 Stock Agent 命令。"
            "你可以换成更明确的说法，例如 `show me latest QQQ signals`、`show health`、"
            "`explain sig-001` 或 `please add QQQ to my watchlist`。"
        )
    return None


def _contains_any(text: str, *markers: str) -> bool:
    return any(marker.lower() in text.lower() for marker in markers)


__all__ = ["ChatClient", "InteractionPlan", "build_interaction_plan", "format_interaction_plan"]

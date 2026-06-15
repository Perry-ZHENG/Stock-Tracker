"""Outbound message safety review for notifications and LLM summaries."""

from __future__ import annotations

from dataclasses import dataclass, field

FORBIDDEN_SUPPRESS_MARKERS = (
    "自动下单",
    "已下单",
    "替你买入",
    "替你卖出",
    "place order",
    "order placed",
)

REPLACEMENTS = (
    ("保证收益", "观察信号不代表收益保证"),
    ("稳赚", "仅供观察"),
    ("必涨", "可能走强"),
    ("必跌", "可能走弱"),
    ("确定买入", "买入观察"),
    ("确定卖出", "卖出观察"),
    ("买入信号", "买入观察信号"),
    ("卖出信号", "卖出观察信号"),
    ("建议买入", "买入观察"),
    ("建议卖出", "卖出观察"),
    ("买入 ", "买入观察 "),
    ("卖出 ", "卖出观察 "),
)


@dataclass(frozen=True)
class MessageSafetyResult:
    ok: bool
    text: str
    suppressed: bool = False
    violations: list[str] = field(default_factory=list)


def review_outbound_message(text: str) -> MessageSafetyResult:
    lowered = text.lower()
    suppressions = [marker for marker in FORBIDDEN_SUPPRESS_MARKERS if marker.lower() in lowered]
    if suppressions:
        return MessageSafetyResult(
            ok=False,
            text="通知已被安全审查拦截：本系统只提供观察信号，不执行或暗示自动交易。",
            suppressed=True,
            violations=suppressions,
        )

    reviewed = text
    violations: list[str] = []
    for before, after in REPLACEMENTS:
        if before in reviewed:
            reviewed = reviewed.replace(before, after)
            violations.append(before)

    return MessageSafetyResult(
        ok=True,
        text=reviewed,
        suppressed=False,
        violations=violations,
    )


__all__ = ["MessageSafetyResult", "review_outbound_message"]

"""Human-readable notification formatting shared by CLI and Telegram sinks."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from stock_agent.schemas import Signal


@dataclass(frozen=True)
class NotificationGroup:
    symbol: str
    timestamp: str
    signal_ids: list[str]
    signals: list[Signal]
    message: str


def group_signals(signals: list[Signal]) -> list[NotificationGroup]:
    grouped: dict[tuple[str, str], list[Signal]] = defaultdict(list)
    for signal in signals:
        timestamp = signal.timestamp.isoformat().replace("+00:00", "Z")
        grouped[(signal.symbol, timestamp)].append(signal)

    groups: list[NotificationGroup] = []
    for (symbol, timestamp), group_signals_ in sorted(grouped.items(), key=lambda item: item[0]):
        sorted_signals = sorted(group_signals_, key=lambda signal: signal.strategy_id)
        groups.append(
            NotificationGroup(
                symbol=symbol,
                timestamp=timestamp,
                signal_ids=[signal.signal_id for signal in sorted_signals],
                signals=sorted_signals,
                message=format_signal_group(sorted_signals),
            )
        )
    return groups


def format_signal_group(signals: list[Signal]) -> str:
    if not signals:
        return "No approved signals."

    first = signals[0]
    timestamp = first.timestamp.isoformat().replace("+00:00", "Z")
    directions = sorted({signal.direction for signal in signals})
    lines = [
        (
            f"{timestamp} {first.symbol} signal alert: "
            f"{len(signals)} strategy trigger(s), direction={','.join(directions)}"
        )
    ]
    for signal in sorted(signals, key=lambda item: item.strategy_id):
        lines.append(
            " | ".join(
                [
                    signal.strategy_id,
                    signal.direction,
                    f"strength={signal.strength:.2f}",
                    f"confidence={signal.confidence:.2f}",
                    signal.reason,
                ]
            )
        )
    return "\n".join(lines)


def format_signal_message(signals: list[Signal]) -> str:
    groups = group_signals(signals)
    if not groups:
        return "No approved signals."
    return "\n\n".join(group.message for group in groups)


__all__ = ["NotificationGroup", "format_signal_group", "format_signal_message", "group_signals"]

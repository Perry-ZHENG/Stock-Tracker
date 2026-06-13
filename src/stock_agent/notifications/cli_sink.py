"""CLI notification sink."""

from __future__ import annotations

import sys
from typing import TextIO

from stock_agent.notifications.base import NotificationResult
from stock_agent.notifications.formatter import format_signal_message
from stock_agent.schemas import Signal


class CliNotificationSink:
    channel = "cli"

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stdout

    def send(self, signals: list[Signal]) -> NotificationResult:
        self.stream.write(format_signal_message(signals))
        self.stream.write("\n")
        self.stream.flush()
        return NotificationResult(channel=self.channel, success=True, attempts=1)

    def send_payload(self, payload: dict[str, object]) -> NotificationResult:
        self.stream.write(str(payload.get("message") or "No approved signals."))
        self.stream.write("\n")
        self.stream.flush()
        return NotificationResult(channel=self.channel, success=True, attempts=1)


__all__ = ["CliNotificationSink", "format_signal_message"]

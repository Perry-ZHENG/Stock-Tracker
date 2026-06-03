"""Notification abstractions shared by CLI, repository, and future Telegram sinks."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Protocol

from stock_agent.schemas import Signal
from stock_agent.storage.repositories import insert_signal


@dataclass(frozen=True)
class NotificationResult:
    channel: str
    success: bool
    attempts: int = 1
    status: str = "sent"
    error_msg: str | None = None


class NotificationSink(Protocol):
    channel: str

    def send(self, signals: list[Signal]) -> NotificationResult:
        """Send or record a group of approved signals."""


class DisabledNotificationSink:
    """Safe sink for unconfigured optional channels such as Telegram in demo mode."""

    def __init__(self, channel: str, reason: str) -> None:
        self.channel = channel
        self.reason = reason

    def send(self, signals: list[Signal]) -> NotificationResult:
        return NotificationResult(
            channel=self.channel,
            success=True,
            attempts=1,
            status="skipped",
            error_msg=self.reason if signals else None,
        )


def persist_approved_signals(
    connection: sqlite3.Connection,
    approved_signals: list[Signal],
) -> None:
    for signal in approved_signals:
        insert_signal(connection, signal)


def send_with_retries(
    sink: NotificationSink,
    signals: list[Signal],
    *,
    max_retries: int = 5,
) -> NotificationResult:
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")

    last_result: NotificationResult | None = None
    for attempt in range(1, max_retries + 1):
        try:
            result = sink.send(signals)
        except Exception as exc:  # pragma: no cover - defensive boundary for external sinks
            result = NotificationResult(
                channel=sink.channel,
                success=False,
                attempts=attempt,
                status="failed",
                error_msg=str(exc),
            )
        last_result = result
        if result.success:
            return NotificationResult(
                channel=result.channel,
                success=True,
                attempts=attempt,
                status=result.status,
                error_msg=result.error_msg,
            )

    assert last_result is not None
    return NotificationResult(
        channel=last_result.channel,
        success=False,
        attempts=max_retries,
        status="failed",
        error_msg=last_result.error_msg,
    )

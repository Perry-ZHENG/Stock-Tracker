"""Notification sinks and signal persistence helpers."""

from stock_agent.notifications.base import (
    DisabledNotificationSink,
    NotificationResult,
    NotificationSink,
    persist_approved_signals,
    send_with_retries,
)
from stock_agent.notifications.cli_sink import CliNotificationSink
from stock_agent.notifications.repository_sink import RepositoryNotificationSink

__all__ = [
    "CliNotificationSink",
    "DisabledNotificationSink",
    "NotificationResult",
    "NotificationSink",
    "RepositoryNotificationSink",
    "persist_approved_signals",
    "send_with_retries",
]

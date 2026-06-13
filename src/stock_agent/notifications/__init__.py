"""Notification sinks and signal persistence helpers."""

from stock_agent.notifications.base import (
    DisabledNotificationSink,
    NotificationResult,
    NotificationSink,
    persist_approved_signals,
    send_with_retries,
)
from stock_agent.notifications.cli_sink import CliNotificationSink
from stock_agent.notifications.formatter import (
    NotificationGroup,
    format_signal_group,
    format_signal_message,
    group_signals,
)
from stock_agent.notifications.outbox import (
    NotificationDeliverySink,
    NotificationOutbox,
    OutboxDispatchResult,
    OutboxEnqueueResult,
    notification_id_for,
)
from stock_agent.notifications.repository_sink import RepositoryNotificationSink

__all__ = [
    "CliNotificationSink",
    "DisabledNotificationSink",
    "NotificationDeliverySink",
    "NotificationGroup",
    "NotificationOutbox",
    "NotificationResult",
    "NotificationSink",
    "OutboxDispatchResult",
    "OutboxEnqueueResult",
    "RepositoryNotificationSink",
    "format_signal_group",
    "format_signal_message",
    "group_signals",
    "notification_id_for",
    "persist_approved_signals",
    "send_with_retries",
]

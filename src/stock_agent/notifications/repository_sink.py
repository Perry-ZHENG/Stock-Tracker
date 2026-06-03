"""SQLite-backed notification sink."""

from __future__ import annotations

import hashlib
import sqlite3

from stock_agent.notifications.base import NotificationResult
from stock_agent.schemas import Signal
from stock_agent.storage.repositories import insert_notification
from stock_agent.tracing import utc_now


class RepositoryNotificationSink:
    channel = "repository"

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def send(self, signals: list[Signal]) -> NotificationResult:
        payload = {
            "signal_ids": [signal.signal_id for signal in signals],
            "signals": [signal.model_dump(mode="json") for signal in signals],
        }
        now = utc_now()
        insert_notification(
            self.connection,
            notification_id=_notification_id(self.channel, payload["signal_ids"]),
            channel=self.channel,
            status="sent",
            payload=payload,
            retry_count=0,
            error_msg=None,
            created_at=now,
            updated_at=now,
        )
        return NotificationResult(channel=self.channel, success=True, attempts=1)


def _notification_id(channel: str, signal_ids: list[str]) -> str:
    payload = "|".join([channel, *signal_ids]) or channel
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"notif-{channel}-{digest}"


__all__ = ["RepositoryNotificationSink"]

"""Notification outbox with idempotent grouped delivery."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Protocol

from stock_agent.notifications.base import NotificationResult
from stock_agent.notifications.formatter import group_signals
from stock_agent.schemas import Signal
from stock_agent.storage.repositories import (
    get_notification,
    insert_notification,
    list_notifications_by_status,
    update_notification_status,
)
from stock_agent.supervisor.message_safety import review_outbound_message
from stock_agent.tracing import utc_now

OUTBOX_RETRYABLE_STATUSES = ("pending", "failed")
OUTBOX_TERMINAL_STATUSES = ("sent", "suppressed")


class NotificationDeliverySink(Protocol):
    channel: str

    def send_payload(self, payload: dict[str, object]) -> NotificationResult:
        """Send a prepared outbox payload."""


@dataclass(frozen=True)
class OutboxEnqueueResult:
    created: int
    existing: int
    notification_ids: list[str]


@dataclass(frozen=True)
class OutboxDispatchResult:
    sent: int = 0
    failed: int = 0
    suppressed: int = 0
    skipped: int = 0


class NotificationOutbox:
    def __init__(self, connection: sqlite3.Connection, *, instance_id: str | None = None) -> None:
        self.connection = connection
        self.instance_id = instance_id

    def enqueue_signals(
        self,
        signals: list[Signal],
        *,
        channels: list[str],
    ) -> OutboxEnqueueResult:
        created = 0
        existing = 0
        notification_ids: list[str] = []
        for group in group_signals(signals):
            for channel in channels:
                notification_id = notification_id_for(channel=channel, signal_ids=group.signal_ids)
                notification_ids.append(notification_id)
                if get_notification(self.connection, notification_id) is not None:
                    existing += 1
                    continue
                now = utc_now()
                safety = review_outbound_message(group.message)
                insert_notification(
                    self.connection,
                    notification_id=notification_id,
                    channel=channel,
                    status="suppressed" if safety.suppressed else "pending",
                    payload={
                        "type": "signal_alert",
                        "symbol": group.symbol,
                        "timestamp": group.timestamp,
                        "signal_ids": group.signal_ids,
                        "message": safety.text,
                        "message_safety": {
                            "ok": safety.ok,
                            "suppressed": safety.suppressed,
                            "violations": safety.violations,
                        },
                        "instance_id": self.instance_id,
                        "signals": [signal.model_dump(mode="json") for signal in group.signals],
                    },
                    retry_count=0,
                    error_msg="message suppressed by safety review" if safety.suppressed else None,
                    created_at=now,
                    updated_at=now,
                )
                created += 1
        return OutboxEnqueueResult(created=created, existing=existing, notification_ids=notification_ids)

    def dispatch_pending(
        self,
        sinks: dict[str, NotificationDeliverySink],
        *,
        max_retries: int = 5,
    ) -> OutboxDispatchResult:
        sent = failed = suppressed = skipped = 0
        rows = list_notifications_by_status(
            self.connection,
            statuses=list(OUTBOX_RETRYABLE_STATUSES),
            limit=500,
        )
        for row in rows:
            notification_id = str(row["notification_id"])
            channel = str(row["channel"])
            retry_count = int(row["retry_count"])
            if retry_count >= max_retries:
                update_notification_status(
                    self.connection,
                    notification_id=notification_id,
                    status="suppressed",
                    retry_count=retry_count,
                    error_msg=row.get("error_msg") or "max retries reached",
                    updated_at=utc_now(),
                )
                suppressed += 1
                continue

            sink = sinks.get(channel)
            if sink is None:
                update_notification_status(
                    self.connection,
                    notification_id=notification_id,
                    status="suppressed",
                    retry_count=retry_count,
                    error_msg=f"no sink configured for channel {channel}",
                    updated_at=utc_now(),
                )
                suppressed += 1
                continue

            update_notification_status(
                self.connection,
                notification_id=notification_id,
                status="sending",
                retry_count=retry_count,
                error_msg=None,
                updated_at=utc_now(),
            )
            try:
                result = sink.send_payload(row["payload"])
            except Exception as exc:  # pragma: no cover - defensive boundary for external sinks
                result = NotificationResult(
                    channel=channel,
                    success=False,
                    attempts=1,
                    status="failed",
                    error_msg=str(exc),
                )

            next_retry_count = retry_count + 1
            if result.success:
                update_notification_status(
                    self.connection,
                    notification_id=notification_id,
                    status="sent" if result.status != "skipped" else "suppressed",
                    retry_count=next_retry_count,
                    error_msg=result.error_msg,
                    updated_at=utc_now(),
                )
                if result.status == "skipped":
                    suppressed += 1
                else:
                    sent += 1
                continue

            final_status = "failed" if next_retry_count < max_retries else "suppressed"
            update_notification_status(
                self.connection,
                notification_id=notification_id,
                status=final_status,
                retry_count=next_retry_count,
                error_msg=result.error_msg,
                updated_at=utc_now(),
            )
            if final_status == "failed":
                failed += 1
            else:
                suppressed += 1

        return OutboxDispatchResult(sent=sent, failed=failed, suppressed=suppressed, skipped=skipped)


def notification_id_for(*, channel: str, signal_ids: list[str]) -> str:
    payload = "|".join([channel, *sorted(signal_ids)])
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"notif-{channel}-{digest}"


__all__ = [
    "NotificationDeliverySink",
    "NotificationOutbox",
    "OutboxDispatchResult",
    "OutboxEnqueueResult",
    "notification_id_for",
]

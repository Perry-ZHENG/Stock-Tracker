import sqlite3
import unittest
from datetime import UTC, datetime

from stock_agent.notifications.outbox import NotificationOutbox
from stock_agent.schemas import Signal
from stock_agent.storage.repositories import get_notification
from stock_agent.supervisor.message_safety import review_outbound_message


class MessageSafetyTests(unittest.TestCase):
    def test_review_rewrites_guaranteed_language_to_observation_language(self) -> None:
        result = review_outbound_message("QQQ 保证收益，建议买入")

        self.assertTrue(result.ok)
        self.assertFalse(result.suppressed)
        self.assertNotIn("保证收益", result.text)
        self.assertIn("买入观察", result.text)

    def test_review_suppresses_auto_trading_language(self) -> None:
        result = review_outbound_message("QQQ 已自动下单买入")

        self.assertFalse(result.ok)
        self.assertTrue(result.suppressed)
        self.assertIn("安全审查拦截", result.text)

    def test_outbox_stores_sanitized_message_without_changing_signal_reason(self) -> None:
        connection = initialize_database_in_memory()
        signal = _signal(reason="保证收益，建议买入")
        result = NotificationOutbox(connection).enqueue_signals([signal], channels=["cli"])
        notification = get_notification(connection, result.notification_ids[0])
        connection.close()

        payload = notification["payload"]
        self.assertNotIn("保证收益", payload["message"])
        self.assertIn("买入观察", payload["message"])
        self.assertEqual(payload["signals"][0]["reason"], "保证收益，建议买入")
        self.assertEqual(notification["status"], "pending")

    def test_outbox_suppresses_auto_trading_message(self) -> None:
        connection = initialize_database_in_memory()
        signal = _signal(reason="已自动下单买入")
        result = NotificationOutbox(connection).enqueue_signals([signal], channels=["cli"])
        notification = get_notification(connection, result.notification_ids[0])
        connection.close()

        self.assertEqual(notification["status"], "suppressed")
        self.assertTrue(notification["payload"]["message_safety"]["suppressed"])


def initialize_database_in_memory():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    from stock_agent.storage.sqlite import _create_tables

    _create_tables(connection)
    return connection


def _signal(*, reason: str) -> Signal:
    return Signal(
        signal_id="sig-qqq-test-20260522T153000Z",
        strategy_id="ma_cross",
        symbol="QQQ",
        timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
        direction="buy_watch",
        strength=0.7,
        confidence=0.8,
        reason=reason,
        trace_id="trace-sig-qqq-test-20260522T153000Z",
        source_bar_ids=["bar-001"],
        data_quality="normal",
        created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
    )


if __name__ == "__main__":
    unittest.main()

import io
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from stock_agent.bars import BarBuilder
from stock_agent.notifications import (
    CliNotificationSink,
    DisabledNotificationSink,
    NotificationOutbox,
    NotificationResult,
    format_signal_message,
    group_signals,
    notification_id_for,
    persist_approved_signals,
    send_with_retries,
)
from stock_agent.notifications.repository_sink import RepositoryNotificationSink
from stock_agent.providers.csv_demo import CsvDemoProvider
from stock_agent.storage.repositories import list_notifications, list_signals
from stock_agent.storage.sqlite import initialize_database
from stock_agent.strategies.ma_cross_demo import generate_ma_cross_demo_signals

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_BARS_PATH = PROJECT_ROOT / "data" / "sample" / "sample_bars.csv"


class NotificationTests(unittest.TestCase):
    def test_persist_approved_signals_writes_sqlite(self) -> None:
        signals = _sample_signals()
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "stock_agent.sqlite")

            persist_approved_signals(connection, signals)

            self.assertEqual(list_signals(connection), signals)

    def test_repository_sink_writes_notification_row(self) -> None:
        signals = _sample_signals()
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "stock_agent.sqlite")
            sink = RepositoryNotificationSink(connection)

            result = send_with_retries(sink, signals)
            notifications = list_notifications(connection)

        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["channel"], "repository")
        self.assertEqual(notifications[0]["payload"]["signal_ids"], [signals[0].signal_id])

    def test_cli_sink_prints_human_readable_signal(self) -> None:
        stream = io.StringIO()
        signals = _sample_signals()

        result = CliNotificationSink(stream).send(signals)

        output = stream.getvalue()
        self.assertTrue(result.success)
        self.assertIn("signal alert", output)
        self.assertIn("ma_cross_demo_2_3", output)
        self.assertIn("buy_watch", output)

    def test_formatter_merges_same_symbol_timestamp_multi_strategy_signals(self) -> None:
        first = _sample_signals()[0]
        second = first.model_copy(
            update={
                "signal_id": "sig-second",
                "strategy_id": "boll",
                "reason": "BOLL 开口",
            }
        )

        groups = group_signals([first, second])
        message = format_signal_message([first, second])

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].signal_ids, [second.signal_id, first.signal_id])
        self.assertIn("2 strategy trigger(s)", message)
        self.assertIn("boll", message)
        self.assertIn("ma_cross_demo_2_3", message)

    def test_outbox_enqueue_is_idempotent_and_dispatches_pending(self) -> None:
        signals = _sample_signals()
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "stock_agent.sqlite")
            outbox = NotificationOutbox(connection)

            first = outbox.enqueue_signals(signals, channels=["cli"])
            second = outbox.enqueue_signals(signals, channels=["cli"])
            dispatch = outbox.dispatch_pending({"cli": CliNotificationSink(stream)})
            notifications = list_notifications(connection)

        self.assertEqual(first.created, 1)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.existing, 1)
        self.assertEqual(dispatch.sent, 1)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["status"], "sent")
        self.assertEqual(notifications[0]["payload"]["signal_ids"], [signals[0].signal_id])
        self.assertIn("signal alert", stream.getvalue())

    def test_outbox_retries_failed_notifications_and_then_suppresses(self) -> None:
        signals = _sample_signals()
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "stock_agent.sqlite")
            outbox = NotificationOutbox(connection)
            outbox.enqueue_signals(signals, channels=["failing"])

            results = [
                outbox.dispatch_pending({"failing": AlwaysFailPayloadSink()}, max_retries=5)
                for _ in range(5)
            ]
            notifications = list_notifications(connection)

        self.assertEqual(sum(result.failed for result in results), 4)
        self.assertEqual(sum(result.suppressed for result in results), 1)
        self.assertEqual(notifications[0]["status"], "suppressed")
        self.assertEqual(notifications[0]["retry_count"], 5)
        self.assertEqual(notifications[0]["error_msg"], "temporary payload failure")

    def test_notification_id_is_stable_for_signal_order(self) -> None:
        self.assertEqual(
            notification_id_for(channel="cli", signal_ids=["b", "a"]),
            notification_id_for(channel="cli", signal_ids=["a", "b"]),
        )

    def test_send_with_retries_stops_after_five_failures(self) -> None:
        sink = AlwaysFailSink()

        result = send_with_retries(sink, _sample_signals(), max_retries=5)

        self.assertFalse(result.success)
        self.assertEqual(result.attempts, 5)
        self.assertEqual(sink.calls, 5)

    def test_disabled_telegram_sink_does_not_block_demo(self) -> None:
        sink = DisabledNotificationSink(
            channel="telegram",
            reason="TELEGRAM_BOT_TOKEN is not configured",
        )

        result = send_with_retries(sink, _sample_signals())

        self.assertTrue(result.success)
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.attempts, 1)


@dataclass
class AlwaysFailSink:
    channel: str = "failing"
    calls: int = 0

    def send(self, _signals):
        self.calls += 1
        return NotificationResult(
            channel=self.channel,
            success=False,
            attempts=1,
            status="failed",
            error_msg="temporary sink failure",
        )


@dataclass
class AlwaysFailPayloadSink:
    channel: str = "failing"

    def send_payload(self, _payload):
        return NotificationResult(
            channel=self.channel,
            success=False,
            attempts=1,
            status="failed",
            error_msg="temporary payload failure",
        )


def _sample_signals():
    bars = BarBuilder().from_standard_bars(
        CsvDemoProvider(SAMPLE_BARS_PATH).fetch_intraday_bars()
    )
    return generate_ma_cross_demo_signals(bars)


if __name__ == "__main__":
    unittest.main()

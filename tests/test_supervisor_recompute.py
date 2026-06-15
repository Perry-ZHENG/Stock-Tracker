import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.schemas import Bar, Signal
from stock_agent.storage.repositories import list_health_metrics, list_trace_chain
from stock_agent.storage.sqlite import initialize_database
from stock_agent.strategies.ma_cross_demo import generate_ma_cross_demo_signals
from stock_agent.supervisor.checks import signal_traces, supervise_candidate_signals
from stock_agent.supervisor.recompute import recompute_signal


class SupervisorRecomputeTests(unittest.TestCase):
    def test_recompute_matches_demo_ma_signal(self) -> None:
        bars = _bars([10, 9, 8, 12, 13])
        signal = generate_ma_cross_demo_signals(bars)[0]

        check = recompute_signal(signal, bars=bars)

        self.assertEqual(check.status, "match")
        self.assertEqual(check.expected_direction, "buy_watch")
        self.assertIn("current_short_ma", check.details)

    def test_supervisor_rejects_signal_when_recompute_disagrees_and_records_audit(self) -> None:
        bars = _bars([10, 9, 8, 12, 13])
        original = generate_ma_cross_demo_signals(bars)[0]
        tampered = original.model_copy(update={"direction": "sell_watch"})

        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.sqlite")
            result = supervise_candidate_signals(
                bars=bars,
                candidate_signals=[tampered],
                traces=signal_traces([tampered]),
                connection=connection,
            )
            traces = list_trace_chain(connection, limit=10)
            metrics = list_health_metrics(connection, limit=10)
            connection.close()

        self.assertFalse(result.ok)
        self.assertEqual(result.approved_signals, [])
        self.assertEqual(result.rejected_signals, [tampered])
        self.assertTrue(any(trace.module == "supervisor_recompute" and trace.status == "failed" for trace in traces))
        self.assertTrue(any(metric.module == "supervisor" and metric.status == "unhealthy" for metric in metrics))

    def test_supervisor_records_successful_recompute_health(self) -> None:
        bars = _bars([10, 9, 8, 12, 13])
        signal = generate_ma_cross_demo_signals(bars)[0]

        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.sqlite")
            result = supervise_candidate_signals(
                bars=bars,
                candidate_signals=[signal],
                traces=signal_traces([signal]),
                connection=connection,
            )
            metrics = list_health_metrics(connection, limit=10)
            connection.close()

        self.assertTrue(result.ok)
        self.assertEqual(result.approved_signals, [signal])
        self.assertTrue(any(metric.module == "supervisor" and metric.status == "healthy" for metric in metrics))


def _bars(closes: list[float]) -> list[Bar]:
    start = datetime(2026, 5, 22, 14, 0, tzinfo=UTC)
    bars = []
    for index, close in enumerate(closes):
        timestamp = start + timedelta(minutes=30 * index)
        bars.append(
            Bar(
                bar_id=f"QQQ-30m-{timestamp.isoformat().replace('+00:00', 'Z')}-demo",
                symbol="QQQ",
                timestamp=timestamp,
                interval="30m",
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1000 + index,
                source="demo",
            )
        )
    return bars


if __name__ == "__main__":
    unittest.main()

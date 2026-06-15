import io
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from stock_agent.commands.query_cli import run_cli_query
from stock_agent.health import record_health_metric
from stock_agent.knowledge import generate_signal_statistics, persist_signal_statistics
from stock_agent.schemas import Signal
from stock_agent.storage.repositories import insert_signal, list_signal_statistics
from stock_agent.storage.sqlite import initialize_runtime_database


class StatisticsTests(unittest.TestCase):
    def test_generates_daily_signal_and_run_statistics_without_pnl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_runtime_database(Path(tmp_dir))
            insert_signal(connection, _signal("sig-buy", "buy_watch"))
            insert_signal(connection, _signal("sig-observe", "observe"))
            record_health_metric(
                connection,
                module="worker",
                now=datetime(2026, 5, 22, 15, 45, tzinfo=UTC),
            )

            statistic = generate_signal_statistics(
                connection,
                period="day",
                anchor=datetime(2026, 5, 22, 16, 0, tzinfo=UTC),
            )
            connection.close()

        self.assertEqual(statistic.signal_count, 2)
        self.assertEqual(statistic.trigger_count, 1)
        self.assertEqual(statistic.run_count, 1)
        self.assertIsNone(statistic.hit_count)
        self.assertEqual(statistic.details["hit_count_status"], "reserved_not_calculated")
        self.assertEqual(statistic.details["excluded_metrics"], ["returns", "holdings", "PnL"])

    def test_persists_monthly_and_yearly_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_runtime_database(Path(tmp_dir))
            insert_signal(connection, _signal("sig-buy", "buy_watch"))

            monthly = generate_signal_statistics(
                connection,
                period="month",
                anchor=datetime(2026, 5, 22, 16, 0, tzinfo=UTC),
            )
            yearly = generate_signal_statistics(
                connection,
                period="year",
                anchor=datetime(2026, 5, 22, 16, 0, tzinfo=UTC),
            )
            persist_signal_statistics(connection, monthly)
            persist_signal_statistics(connection, yearly)
            stored_monthly = list_signal_statistics(connection, period="month")
            stored_yearly = list_signal_statistics(connection, period="year")
            connection.close()

        self.assertEqual(stored_monthly[0]["period"], "month")
        self.assertEqual(stored_monthly[0]["signal_count"], 1)
        self.assertEqual(stored_yearly[0]["period"], "year")
        self.assertEqual(stored_yearly[0]["signal_count"], 1)

    def test_cli_stats_generates_and_queries_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            insert_signal(connection, _signal("sig-buy", "buy_watch"))
            connection.close()
            stream = io.StringIO()

            exit_code = run_cli_query(root, query="stats", period="day", stream=stream)

        self.assertEqual(exit_code, 0)
        self.assertIn("period | period_start | signal_count", stream.getvalue())
        self.assertIn("reserved_not_calculated", stream.getvalue())

    def test_cli_stats_rejects_unknown_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            connection.close()
            stream = io.StringIO()

            exit_code = run_cli_query(root, query="stats", period="week", stream=stream)

        self.assertEqual(exit_code, 1)
        self.assertIn("query_error=unsupported stats period week", stream.getvalue())


def _signal(signal_id: str, direction: str) -> Signal:
    return Signal(
        signal_id=signal_id,
        strategy_id="ma_cross",
        symbol="QQQ",
        timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
        direction=direction,  # type: ignore[arg-type]
        strength=0.7,
        confidence=0.8,
        reason="test statistic",
        trace_id=f"trace-{signal_id}",
        source_bar_ids=["bar-001"],
        data_quality="normal",
        created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
    )


if __name__ == "__main__":
    unittest.main()

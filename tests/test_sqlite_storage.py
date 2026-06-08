import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from stock_agent.schemas import HealthMetric, Signal, TraceChain
from stock_agent.storage.repositories import (
    get_health_metric,
    get_signal,
    get_trace_chain,
    insert_health_metric,
    insert_signal,
    insert_trace_chain,
    list_health_metrics,
    list_signals,
)
from stock_agent.storage.sqlite import REQUIRED_TABLES, initialize_database, initialize_runtime_database


class SqliteStorageTests(unittest.TestCase):
    def test_initialize_database_creates_required_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "stock_agent.sqlite")
            table_names = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

            self.assertTrue(set(REQUIRED_TABLES).issubset(table_names))

    def test_open_database_ensures_tables_for_existing_runtime_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "stock_agent.sqlite"
            db_path.touch()

            connection = initialize_database(db_path)
            table_names = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

            self.assertIn("signal_statistics", table_names)

    def test_initialize_runtime_database_uses_default_demo_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)

            self.assertTrue((root / "data" / "runtime" / "stock_agent.sqlite").exists())
            self.assertIsInstance(connection, sqlite3.Connection)

    def test_signal_repository_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "stock_agent.sqlite")
            signal = Signal(
                signal_id="sig-001",
                strategy_id="ma_cross_demo_2_3",
                symbol="QQQ",
                timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
                direction="buy_watch",
                strength=0.7,
                confidence=0.9,
                reason="MA2 crossed above MA3",
                trace_id="trace-001",
                source_bar_ids=["bar-001", "bar-002", "bar-003"],
                data_quality="normal",
                created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
            )

            insert_signal(connection, signal)

            stored = get_signal(connection, "sig-001")
            self.assertIsNotNone(stored)
            self.assertEqual(stored, signal)
            self.assertEqual(list_signals(connection), [signal])

    def test_trace_chain_repository_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "stock_agent.sqlite")
            trace = TraceChain(
                trace_id="trace-001",
                parent_id=None,
                module="strategy_engine",
                input_ref=["bar-001", "bar-002"],
                output_ref=["sig-001"],
                status="success",
                error_msg=None,
                created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
            )

            insert_trace_chain(connection, trace)

            self.assertEqual(get_trace_chain(connection, "trace-001"), trace)

    def test_health_metric_repository_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "stock_agent.sqlite")
            metric = HealthMetric(
                metric_id="health-001",
                timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
                module="market_watch",
                heartbeat_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
                data_latency_sec=1.2,
                error_rate=0.01,
                consecutive_failures=0,
                alert_failures=0,
                status="healthy",
                details={"provider": "demo_csv"},
            )

            insert_health_metric(connection, metric)

            stored = get_health_metric(connection, "health-001")
            self.assertIsNotNone(stored)
            self.assertEqual(stored, metric)
            self.assertEqual(list_health_metrics(connection), [metric])


if __name__ == "__main__":
    unittest.main()

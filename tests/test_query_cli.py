import io
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from stock_agent.cli import main
from stock_agent.commands.query_cli import run_cli_query
from stock_agent.schemas import HealthMetric, Signal
from stock_agent.storage.repositories import insert_health_metric, insert_signal
from stock_agent.storage.sqlite import initialize_runtime_database


class QueryCliTests(unittest.TestCase):
    def test_cli_without_query_prints_available_queries(self) -> None:
        stream = io.StringIO()

        exit_code = run_cli_query(Path("/unused"), stream=stream)

        self.assertEqual(exit_code, 0)
        self.assertIn("Available queries", stream.getvalue())
        self.assertIn("stats", stream.getvalue())

    def test_queries_recent_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            insert_signal(connection, _signal())
            stream = io.StringIO()

            exit_code = run_cli_query(root, query="signals", stream=stream)

        self.assertEqual(exit_code, 0)
        self.assertIn("strategy_id", stream.getvalue())
        self.assertIn("ma_cross", stream.getvalue())

    def test_queries_health(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            insert_health_metric(connection, _health_metric())
            stream = io.StringIO()

            exit_code = run_cli_query(root, query="health", stream=stream)

        self.assertEqual(exit_code, 0)
        self.assertIn("healthy", stream.getvalue())
        self.assertIn("run_demo", stream.getvalue())

    def test_queries_config_changes_and_news_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            connection.execute(
                """
                INSERT INTO config_changes (
                    change_id, status, source, before_config, after_config, diff, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "chg-001",
                    "pending_review",
                    "telegram",
                    "{}",
                    "{}",
                    "symbols +QQQ",
                    "2026-05-22T15:30:00Z",
                    "2026-05-22T15:30:00Z",
                ),
            )
            connection.execute(
                """
                INSERT INTO news_items (
                    news_id, symbol, market, title, summary, url, source,
                    published_at, retention_level, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "news-001",
                    "QQQ",
                    "US",
                    "QQQ news",
                    "summary",
                    "https://example.com/news",
                    "unit_test",
                    "2026-05-22T12:00:00Z",
                    "raw_summary",
                    "2026-05-22T12:01:00Z",
                ),
            )
            connection.commit()
            config_stream = io.StringIO()
            news_stream = io.StringIO()

            config_exit = run_cli_query(root, query="config-changes", stream=config_stream)
            news_exit = run_cli_query(root, query="news", stream=news_stream)

        self.assertEqual(config_exit, 0)
        self.assertEqual(news_exit, 0)
        self.assertIn("chg-001", config_stream.getvalue())
        self.assertIn("QQQ news", news_stream.getvalue())

    def test_news_query_without_provider_returns_readable_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_runtime_database(root)
            stream = io.StringIO()

            exit_code = run_cli_query(root, query="news", symbol="QQQ", stream=stream)

        self.assertEqual(exit_code, 0)
        self.assertIn("news_status=unavailable", stream.getvalue())

    def test_missing_database_returns_readable_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            stream = io.StringIO()

            exit_code = run_cli_query(Path(tmp_dir), query="signals", stream=stream)

        self.assertEqual(exit_code, 1)
        self.assertIn("query_error=no runtime database", stream.getvalue())

    def test_stock_agent_cli_signal_query_uses_real_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            insert_signal(connection, _signal())

            with patch("pathlib.Path.cwd", return_value=root):
                self.assertEqual(main(["cli", "signals"]), 0)


def _signal() -> Signal:
    return Signal(
        signal_id="sig-001",
        strategy_id="ma_cross",
        symbol="QQQ",
        timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
        direction="buy_watch",
        strength=0.7,
        confidence=0.8,
        reason="MA3 上穿 MA5",
        trace_id="trace-sig-001",
        source_bar_ids=["bar-001"],
        data_quality="normal",
        created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
    )


def _health_metric() -> HealthMetric:
    return HealthMetric(
        metric_id="health-001",
        timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
        module="run_demo",
        heartbeat_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
        data_latency_sec=0,
        error_rate=0,
        consecutive_failures=0,
        alert_failures=0,
        status="healthy",
        details={},
    )


if __name__ == "__main__":
    unittest.main()

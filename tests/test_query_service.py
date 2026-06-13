import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from stock_agent.health import record_health_metric
from stock_agent.query import QueryService
from stock_agent.schemas import Bar, NewsItem, Signal, TraceChain
from stock_agent.storage import LakeWriter
from stock_agent.storage.repositories import insert_news_item, insert_signal, insert_trace_chain
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.telegram import handle_telegram_message


class QueryServiceTests(unittest.TestCase):
    def test_returns_readable_error_when_runtime_database_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = QueryService(Path(tmp_dir)).execute("signals")

        self.assertFalse(result.ok)
        self.assertIn("query_error=no runtime database", result.text)

    def test_queries_core_runtime_tables_and_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            signal = _signal()
            insert_signal(connection, signal)
            insert_trace_chain(connection, _trace(signal))
            record_health_metric(connection, module="unit_test", data_latency_sec=0, error_rate=0)
            insert_news_item(connection, _news_item())
            connection.close()
            service = QueryService(root)

            signals = service.execute("signals")
            health = service.execute("health")
            news = service.execute("news", symbol="QQQ")
            stats = service.execute("stats", period="day")
            trace = service.execute("trace", target_id=signal.signal_id)
            schedule = service.execute("schedule")

        self.assertTrue(signals.ok)
        self.assertIn("sig-001", signals.text)
        self.assertIn("unit_test", health.text)
        self.assertIn("QQQ news", news.text)
        self.assertIn("day", stats.text)
        self.assertIn("trace_status=ok", trace.text)
        self.assertIn("schedule_date=", schedule.text)

    def test_queries_bars_from_lake(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            LakeWriter(root / "data/lake").write_raw_bars([_bar()])

            result = QueryService(root).execute(
                "bars",
                symbol="QQQ",
                from_value="2026-05-22",
                to_value="2026-05-22",
            )

        self.assertTrue(result.ok)
        self.assertIn("QQQ", result.text)
        self.assertEqual(len(result.rows), 1)

    def test_telegram_and_service_share_same_query_core(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            insert_signal(connection, _signal())
            connection.close()

            service_result = QueryService(root).execute("signals", output_format="telegram")
            telegram_result = handle_telegram_message(
                root=root,
                connection=connection,
                user_id=1,
                text="/signals",
                allowed_user_ids=[1],
                admin_user_ids=[],
            )

        self.assertTrue(telegram_result.ok)
        self.assertIn("sig-001", service_result.text)
        self.assertIn("sig-001", telegram_result.message)


def _signal() -> Signal:
    return Signal(
        signal_id="sig-001",
        strategy_id="ma_cross",
        symbol="QQQ",
        timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
        direction="buy_watch",
        strength=0.7,
        confidence=0.8,
        reason="MA3 crossed above MA5",
        trace_id="trace-sig-001",
        source_bar_ids=["bar-001", "bar-002"],
        data_quality="normal",
        created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
    )


def _trace(signal: Signal) -> TraceChain:
    return TraceChain(
        trace_id=signal.trace_id,
        parent_id=None,
        module="strategy_engine",
        input_ref=signal.source_bar_ids,
        output_ref=[signal.signal_id],
        status="success",
        error_msg=None,
        created_at=signal.created_at,
    )


def _bar() -> Bar:
    return Bar(
        bar_id="QQQ-30m-2026-05-22T15:30:00Z-unit_test",
        symbol="QQQ",
        timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
        interval="30m",
        open=100,
        high=101,
        low=99,
        close=100.5,
        volume=1000,
        vwap=100.3,
        source="unit_test",
    )


def _news_item() -> NewsItem:
    return NewsItem(
        news_id="news-001",
        symbol="QQQ",
        market="US",
        title="QQQ news",
        summary="summary",
        url="https://example.com/news",
        source="unit_test",
        published_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        retention_level="raw_summary",
        created_at=datetime(2026, 5, 22, 12, 1, tzinfo=UTC),
    )


if __name__ == "__main__":
    unittest.main()

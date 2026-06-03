import unittest
from datetime import UTC, datetime, timezone, timedelta

from pydantic import ValidationError

from stock_agent.schemas import Bar, HealthMetric, NewsItem, Signal, StrategySnapshot, TraceChain


class SchemaTests(unittest.TestCase):
    def test_bar_converts_timestamp_to_utc(self) -> None:
        bar = Bar(
            bar_id="QQQ-30m-2026-05-22T14:00:00Z-demo_csv",
            symbol="QQQ",
            timestamp=datetime(2026, 5, 22, 22, 0, tzinfo=timezone(timedelta(hours=8))),
            open=100,
            high=101,
            low=99,
            close=100.5,
            volume=1000,
            source="demo_csv",
        )

        self.assertEqual(bar.timestamp.tzinfo, UTC)
        self.assertEqual(bar.timestamp.hour, 14)

    def test_signal_requires_standard_fields(self) -> None:
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

        self.assertEqual(signal.direction, "buy_watch")

    def test_signal_rejects_invalid_direction(self) -> None:
        with self.assertRaises(ValidationError):
            Signal(
                signal_id="sig-001",
                strategy_id="ma_cross_demo_2_3",
                symbol="QQQ",
                timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
                direction="buy",
                strength=0.7,
                confidence=0.9,
                reason="invalid direction",
                trace_id="trace-001",
                source_bar_ids=["bar-001"],
                data_quality="normal",
                created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
            )

    def test_signal_rejects_missing_required_field(self) -> None:
        with self.assertRaises(ValidationError):
            Signal(
                signal_id="sig-001",
                strategy_id="ma_cross_demo_2_3",
                timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
                direction="buy_watch",
                strength=0.7,
                confidence=0.9,
                reason="missing symbol",
                trace_id="trace-001",
                source_bar_ids=["bar-001"],
                data_quality="normal",
                created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
            )

    def test_signal_rejects_old_field_names(self) -> None:
        with self.assertRaises(ValidationError):
            Signal(
                signal_id="sig-001",
                strategy_id="ma_cross_demo_2_3",
                symbol="QQQ",
                timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
                direction="observe",
                strength=0.7,
                confidence=0.9,
                reason="extra old fields",
                trace_id="trace-001",
                source_bar_ids=["bar-001"],
                data_quality="normal",
                created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
                strategy="old_name",
                side="buy",
                signal_strength=0.7,
                evidence={},
            )

    def test_naive_datetime_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Bar(
                bar_id="bar-001",
                symbol="QQQ",
                timestamp=datetime(2026, 5, 22, 14, 0),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=100,
            )

    def test_all_schema_models_can_validate_minimal_examples(self) -> None:
        created_at = datetime(2026, 5, 22, 14, 0, tzinfo=UTC)

        TraceChain(
            trace_id="trace-001",
            module="strategy_engine",
            input_ref=["bar-001"],
            output_ref=["sig-001"],
            status="success",
            created_at=created_at,
        )
        StrategySnapshot(
            snapshot_id="snap-001",
            date=created_at.date(),
            enabled_strategies=["ma_cross"],
            symbols=["QQQ"],
            created_at=created_at,
        )
        NewsItem(
            news_id="news-001",
            title="Market update",
            summary="Short summary",
            url="https://example.com/news/1",
            source="demo",
            published_at=created_at,
            created_at=created_at,
        )
        HealthMetric(
            metric_id="health-001",
            timestamp=created_at,
            module="market_watch",
            heartbeat_at=created_at,
            data_latency_sec=1,
            error_rate=0,
            consecutive_failures=0,
            alert_failures=0,
            status="healthy",
        )


if __name__ == "__main__":
    unittest.main()

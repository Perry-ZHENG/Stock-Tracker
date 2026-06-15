import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.query import QueryService
from stock_agent.schemas import Bar
from stock_agent.storage.repositories import list_health_metrics, list_trace_chain
from stock_agent.storage.sqlite import initialize_database
from stock_agent.supervisor.provider_compare import (
    ProviderCompareThresholds,
    apply_compare_quality,
    compare_provider_bars,
    persist_provider_compare,
)
from stock_agent.telegram.listener import handle_telegram_message


class ProviderCompareTests(unittest.TestCase):
    def test_matching_provider_bars_are_ok(self) -> None:
        primary = [_bar(close=100, volume=1000, source="primary")]
        secondary = [_bar(close=100.2, volume=1050, source="secondary")]

        result = compare_provider_bars(
            primary_bars=primary,
            secondary_bars=secondary,
            thresholds=ProviderCompareThresholds(price_diff_bps=50, volume_diff_ratio=0.1),
        )

        self.assertEqual(result.status, "ok")
        self.assertFalse(result.should_suppress_signals)

    def test_provider_deviation_marks_quality_and_suppresses(self) -> None:
        primary = [_bar(close=100, volume=1000, source="primary")]
        secondary = [_bar(close=120, volume=5000, source="secondary")]

        result = compare_provider_bars(
            primary_bars=primary,
            secondary_bars=secondary,
            thresholds=ProviderCompareThresholds(price_diff_bps=50, volume_diff_ratio=0.1),
        )
        marked = apply_compare_quality(primary, result)

        self.assertEqual(result.status, "unhealthy")
        self.assertTrue(result.should_suppress_signals)
        self.assertIn("provider_compare_unhealthy", marked[0].quality_flag)

    def test_missing_secondary_skips_without_blocking_demo(self) -> None:
        result = compare_provider_bars(primary_bars=[_bar()], secondary_bars=None)

        self.assertEqual(result.status, "skipped")
        self.assertTrue(result.skipped)
        self.assertFalse(result.should_suppress_signals)

    def test_compare_persists_trace_health_and_is_queryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_database(root / "data/runtime/stock_agent.sqlite")
            result = compare_provider_bars(
                primary_bars=[_bar(close=100, source="primary")],
                secondary_bars=[_bar(close=120, source="secondary")],
                thresholds=ProviderCompareThresholds(price_diff_bps=50),
            )
            persist_provider_compare(
                connection,
                result,
                primary_provider="primary",
                secondary_provider="secondary",
                thresholds=ProviderCompareThresholds(price_diff_bps=50),
            )
            traces = list_trace_chain(connection)
            metrics = list_health_metrics(connection)
            query = QueryService(root).execute("provider-compare")
            telegram = handle_telegram_message(
                root=root,
                connection=connection,
                user_id=1,
                text="/provider-compare",
                allowed_user_ids=[1],
            )
            connection.close()

        self.assertTrue(any(trace.module == "provider_compare" and trace.status == "failed" for trace in traces))
        self.assertTrue(any(metric.module == "provider_compare" and metric.status == "unhealthy" for metric in metrics))
        self.assertTrue(query.ok)
        self.assertIn("provider_compare", query.text)
        self.assertTrue(telegram.ok)
        self.assertIn("provider_compare", telegram.message)


def _bar(*, close: float = 100, volume: int = 1000, source: str = "primary", offset_sec: int = 0) -> Bar:
    timestamp = datetime(2026, 5, 22, 15, 30, tzinfo=UTC) + timedelta(seconds=offset_sec)
    return Bar(
        bar_id=f"QQQ-30m-{timestamp.isoformat().replace('+00:00', 'Z')}-{source}",
        symbol="QQQ",
        timestamp=timestamp,
        interval="30m",
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=volume,
        source=source,
    )


if __name__ == "__main__":
    unittest.main()

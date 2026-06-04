import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from stock_agent.schemas import Bar, NewsItem, Signal
from stock_agent.storage.lake import LakeWriter


class LakeStorageTests(unittest.TestCase):
    def test_writes_raw_bars_to_date_partition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch("stock_agent.storage.lake._parquet_available", return_value=False):
            result = LakeWriter(Path(tmp_dir)).write_raw_bars([_bar()])

            self.assertEqual(result.dataset, "raw_bars")
            self.assertEqual(result.rows, 1)
            self.assertTrue(result.path.exists())
            self.assertIn("raw_bars/date=2026-05-22", result.path.as_posix())
            self.assertEqual(_read_jsonl(result.path)[0]["symbol"], "QQQ")

    def test_writes_signals_to_date_partition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch("stock_agent.storage.lake._parquet_available", return_value=False):
            result = LakeWriter(Path(tmp_dir)).write_signals([_signal()])

            self.assertEqual(result.dataset, "signals")
            self.assertTrue(result.path.exists())
            self.assertIn("signals/date=2026-05-22", result.path.as_posix())
            self.assertEqual(_read_jsonl(result.path)[0]["signal_id"], "sig-001")

    def test_writes_news_to_date_partition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch("stock_agent.storage.lake._parquet_available", return_value=False):
            result = LakeWriter(Path(tmp_dir)).write_news([_news_item()])

            self.assertEqual(result.dataset, "news")
            self.assertTrue(result.path.exists())
            self.assertIn("news/date=2026-05-22", result.path.as_posix())
            self.assertEqual(_read_jsonl(result.path)[0]["title"], "QQQ news")

    def test_writes_features_to_date_partition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch("stock_agent.storage.lake._parquet_available", return_value=False):
            result = LakeWriter(Path(tmp_dir)).write_features(
                [
                    {
                        "symbol": "QQQ",
                        "timestamp": "2026-05-22T15:30:00Z",
                        "feature": "ma3",
                        "value": 471.2,
                    }
                ]
            )

            self.assertEqual(result.dataset, "features")
            self.assertTrue(result.path.exists())
            self.assertIn("features/date=2026-05-22", result.path.as_posix())
            self.assertEqual(_read_jsonl(result.path)[0]["feature"], "ma3")

    @unittest.expectedFailure
    def test_todo_writes_parquet_when_engine_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = LakeWriter(Path(tmp_dir)).write_raw_bars([_bar()])

            self.assertEqual(result.format, "parquet")
            self.assertEqual(result.path.suffix, ".parquet")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _bar() -> Bar:
    return Bar(
        bar_id="QQQ-30m-2026-05-22T15:30:00Z-unit_test",
        symbol="QQQ",
        timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
        interval="30m",
        open=471,
        high=472,
        low=470,
        close=471.5,
        volume=1000,
        vwap=471.3,
        source="unit_test",
    )


def _signal() -> Signal:
    return Signal(
        signal_id="sig-001",
        strategy_id="ma_cross",
        symbol="QQQ",
        timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
        direction="buy_watch",
        strength=0.7,
        confidence=0.8,
        reason="test signal",
        trace_id="trace-sig-001",
        source_bar_ids=["bar-001"],
        data_quality="normal",
        created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
    )


def _news_item() -> NewsItem:
    return NewsItem(
        news_id="news-001",
        symbol="QQQ",
        market="US",
        title="QQQ news",
        summary="demo news",
        url="https://example.com/news",
        source="unit_test",
        published_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        retention_level="raw_summary",
        created_at=datetime(2026, 5, 22, 12, 1, tzinfo=UTC),
    )


if __name__ == "__main__":
    unittest.main()

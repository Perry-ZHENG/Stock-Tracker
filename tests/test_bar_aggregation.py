import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.bars import (
    BarBuilder,
    aggregate_to_interval,
    build_interpolated_bar,
    checkpoint_id,
    detect_missing_windows,
    update_bar_checkpoint,
)
from stock_agent.bars.validation import generate_bar_id
from stock_agent.schemas import Bar
from stock_agent.storage.repositories import get_checkpoint
from stock_agent.storage.sqlite import initialize_database


class BarAggregationTests(unittest.TestCase):
    def test_aggregates_1m_bars_into_deterministic_30m_bar(self) -> None:
        bars = _minute_bars(
            start=datetime(2026, 5, 22, 13, 31, tzinfo=UTC),
            count=30,
        )

        result = aggregate_to_interval(bars)

        self.assertEqual(len(result.bars), 1)
        aggregated = result.bars[0]
        self.assertEqual(aggregated.timestamp, datetime(2026, 5, 22, 14, 0, tzinfo=UTC))
        self.assertEqual(aggregated.bar_id, "QQQ-30m-2026-05-22T14:00:00Z-unit_1m_agg")
        self.assertEqual(aggregated.open, bars[0].open)
        self.assertEqual(aggregated.high, max(bar.high for bar in bars))
        self.assertEqual(aggregated.low, min(bar.low for bar in bars))
        self.assertEqual(aggregated.close, bars[-1].close)
        self.assertEqual(aggregated.volume, sum(bar.volume for bar in bars))
        self.assertAlmostEqual(aggregated.vwap, _weighted_vwap(bars))
        self.assertEqual(aggregated.quality_flag, "normal")

    def test_marks_missing_duplicate_and_out_of_order_quality(self) -> None:
        missing = _minute_bars(
            start=datetime(2026, 5, 22, 13, 31, tzinfo=UTC),
            count=29,
        )
        duplicate = [*_minute_bars(start=datetime(2026, 5, 22, 13, 31, tzinfo=UTC), count=30)]
        duplicate.append(duplicate[-1])
        out_of_order = list(reversed(_minute_bars(start=datetime(2026, 5, 22, 13, 31, tzinfo=UTC), count=30)))

        missing_bar = aggregate_to_interval(missing).bars[0]
        duplicate_bar = aggregate_to_interval(duplicate).bars[0]
        out_of_order_bar = aggregate_to_interval(out_of_order).bars[0]

        self.assertEqual(missing_bar.quality_flag, "missing")
        self.assertEqual(duplicate_bar.quality_flag, "duplicate")
        self.assertEqual(out_of_order_bar.quality_flag, "out_of_order")

    def test_bar_builder_can_aggregate_source_bars(self) -> None:
        bars = _minute_bars(
            start=datetime(2026, 5, 22, 13, 31, tzinfo=UTC),
            count=30,
        )

        prepared = BarBuilder().from_source_bars(bars)

        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0].interval, "30m")
        self.assertEqual(prepared[0].quality_flag, "normal")

    def test_checkpoint_records_last_successful_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.sqlite")
            update_bar_checkpoint(
                connection,
                symbol="QQQ",
                interval="30m",
                window_end=datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
            )

            checkpoint = get_checkpoint(connection, checkpoint_id(symbol="QQQ", interval="30m"))

        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint["checkpoint_value"], "2026-05-22T14:00:00Z")
        self.assertEqual(checkpoint["checkpoint_key"], "QQQ:30m:last_window_end")

    def test_detects_missing_30m_windows_and_builds_interpolated_bar(self) -> None:
        existing = [
            _thirty_minute_bar(datetime(2026, 5, 22, 14, 0, tzinfo=UTC), close=100),
            _thirty_minute_bar(datetime(2026, 5, 22, 15, 0, tzinfo=UTC), open_=104, close=105),
        ]

        plan = detect_missing_windows(
            existing,
            symbol="QQQ",
            interval="30m",
            start_at=datetime(2026, 5, 22, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 5, 22, 15, 0, tzinfo=UTC),
        )
        interpolated = build_interpolated_bar(
            window=plan.missing_windows[0],
            previous_bar=existing[0],
            next_bar=existing[1],
        )

        self.assertEqual(len(plan.missing_windows), 1)
        self.assertEqual(plan.missing_windows[0].end_at, datetime(2026, 5, 22, 14, 30, tzinfo=UTC))
        self.assertEqual(interpolated.timestamp, datetime(2026, 5, 22, 14, 30, tzinfo=UTC))
        self.assertEqual(interpolated.volume, 0)
        self.assertEqual(interpolated.quality_flag, "interpolated|missing")


def _minute_bars(*, start: datetime, count: int) -> list[Bar]:
    bars: list[Bar] = []
    for offset in range(count):
        timestamp = start + timedelta(minutes=offset)
        price = 100 + offset / 10
        bars.append(
            Bar(
                bar_id=generate_bar_id(
                    "QQQ",
                    "1m",
                    timestamp.isoformat().replace("+00:00", "Z"),
                    "unit_1m",
                ),
                symbol="QQQ",
                timestamp=timestamp,
                interval="1m",
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price + 0.1,
                volume=100 + offset,
                vwap=price + 0.05,
                source="unit_1m",
                quality_flag="normal",
            )
        )
    return bars


def _thirty_minute_bar(
    timestamp: datetime,
    *,
    open_: float = 100,
    close: float = 100,
) -> Bar:
    return Bar(
        bar_id=generate_bar_id("QQQ", "30m", timestamp.isoformat().replace("+00:00", "Z"), "unit_30m"),
        symbol="QQQ",
        timestamp=timestamp,
        interval="30m",
        open=open_,
        high=max(open_, close) + 1,
        low=min(open_, close) - 1,
        close=close,
        volume=1000,
        vwap=(open_ + close) / 2,
        source="unit_30m",
        quality_flag="normal",
    )


def _weighted_vwap(bars: list[Bar]) -> float:
    return sum((bar.vwap or 0) * bar.volume for bar in bars) / sum(bar.volume for bar in bars)


if __name__ == "__main__":
    unittest.main()

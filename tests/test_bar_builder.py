import unittest
from datetime import UTC, datetime

from stock_agent.bars import (
    BarBuilder,
    BarValidationError,
    filter_regular_session,
    generate_bar_id,
    is_regular_session_bar,
    validate_bar,
)
from stock_agent.providers.csv_demo import CsvDemoProvider
from stock_agent.schemas import Bar


def make_bar(
    timestamp: datetime,
    *,
    high: float = 101,
    low: float = 99,
    open_: float = 100,
    close: float = 100,
    vwap: float | None = 100,
) -> Bar:
    timestamp_text = timestamp.isoformat().replace("+00:00", "Z")
    return Bar(
        bar_id=generate_bar_id("QQQ", "30m", timestamp_text, "demo_csv"),
        symbol="QQQ",
        timestamp=timestamp,
        interval="30m",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000,
        vwap=vwap,
        source="demo_csv",
    )


class BarBuilderTests(unittest.TestCase):
    def test_generate_bar_id_is_deterministic(self) -> None:
        self.assertEqual(
            generate_bar_id("QQQ", "30m", "2026-05-22T15:30:00Z", "demo_csv"),
            "QQQ-30m-2026-05-22T15:30:00Z-demo_csv",
        )

    def test_validate_bar_accepts_valid_bar(self) -> None:
        bar = make_bar(datetime(2026, 5, 22, 14, 0, tzinfo=UTC))

        self.assertEqual(validate_bar(bar), bar)

    def test_validate_bar_rejects_abnormal_price_range(self) -> None:
        bar = make_bar(datetime(2026, 5, 22, 14, 0, tzinfo=UTC), high=98, low=99)

        with self.assertRaisesRegex(BarValidationError, "high below low"):
            validate_bar(bar)

    def test_validate_bar_rejects_non_deterministic_id(self) -> None:
        bar = make_bar(datetime(2026, 5, 22, 14, 0, tzinfo=UTC)).model_copy(
            update={"bar_id": "not-deterministic"}
        )

        with self.assertRaisesRegex(BarValidationError, "non-deterministic id"):
            validate_bar(bar)

    def test_regular_session_filter_keeps_default_strategy_bars_only(self) -> None:
        premarket = make_bar(datetime(2026, 5, 22, 12, 0, tzinfo=UTC))
        regular = make_bar(datetime(2026, 5, 22, 13, 30, tzinfo=UTC))
        after_hours = make_bar(datetime(2026, 5, 22, 21, 0, tzinfo=UTC))

        self.assertFalse(is_regular_session_bar(premarket))
        self.assertTrue(is_regular_session_bar(regular))
        self.assertFalse(is_regular_session_bar(after_hours))
        self.assertEqual(filter_regular_session([premarket, regular, after_hours]), [regular])

    def test_builder_passes_through_standard_demo_bars_after_validation(self) -> None:
        bars = CsvDemoProvider("data/sample/sample_bars.csv").fetch_intraday_bars()

        prepared_bars = BarBuilder().from_standard_bars(bars)

        self.assertEqual(prepared_bars, bars)


if __name__ == "__main__":
    unittest.main()

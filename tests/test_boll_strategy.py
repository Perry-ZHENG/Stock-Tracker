import unittest
from datetime import UTC, datetime, timedelta

from stock_agent.bars.validation import generate_bar_id
from stock_agent.schemas import Bar
from stock_agent.strategies.boll import BOLL_STRATEGY_ID, generate_boll_signals


class BollStrategyTests(unittest.TestCase):
    def test_window_plus_one_before_warmup_generates_no_signal(self) -> None:
        bars = _bars_from_closes([10, 10, 10, 10])

        signals = generate_boll_signals(bars, window=3, bandwidth_baseline_window=2)

        self.assertEqual(signals, [])

    def test_widening_above_middle_generates_buy_watch(self) -> None:
        bars = _bars_from_closes([10, 10, 10, 10, 10, 8, 14])

        signals = generate_boll_signals(bars, window=3, bandwidth_baseline_window=2)

        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.strategy_id, BOLL_STRATEGY_ID)
        self.assertEqual(signal.direction, "buy_watch")
        self.assertIn("BOLL 开口", signal.reason)
        self.assertIn("bandwidth=", signal.reason)
        self.assertIn("baseline_bandwidth=", signal.reason)

    def test_stable_narrowing_generates_observe(self) -> None:
        bars = _bars_from_closes([10, 11, 9, 10, 11, 9, 10])

        signals = generate_boll_signals(bars, window=3, bandwidth_baseline_window=2)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].direction, "observe")
        self.assertIn("BOLL 缩口/稳定", signals[0].reason)

    def test_stable_narrowing_below_middle_without_oscillation_generates_sell_watch(self) -> None:
        bars = _bars_from_closes([10, 12, 8, 10, 12, 8, 9])

        signals = generate_boll_signals(bars, window=3, bandwidth_baseline_window=2)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].direction, "sell_watch")
        self.assertIn("跌破中轨", signals[0].reason)
        self.assertIn("无中轨附近震荡", signals[0].reason)

    def test_groups_symbols_before_calculation(self) -> None:
        qqq_bars = _bars_from_closes([10, 10, 10, 10, 10, 8, 14], symbol="QQQ")
        spy_bars = _bars_from_closes([10, 12, 8, 10, 12, 8, 9], symbol="SPY")

        signals = generate_boll_signals([*qqq_bars, *spy_bars], window=3, bandwidth_baseline_window=2)

        self.assertEqual([signal.symbol for signal in signals], ["QQQ", "SPY"])
        self.assertEqual([signal.direction for signal in signals], ["buy_watch", "sell_watch"])

    def test_invalid_params_rejected(self) -> None:
        with self.assertRaises(ValueError):
            generate_boll_signals(_bars_from_closes([10] * 7), window=0)


def _bars_from_closes(closes: list[float], *, symbol: str = "QQQ") -> list[Bar]:
    start = datetime(2026, 5, 22, 13, 30, tzinfo=UTC)
    bars: list[Bar] = []
    for index, close in enumerate(closes):
        timestamp = start + timedelta(minutes=30 * index)
        timestamp_text = timestamp.isoformat().replace("+00:00", "Z")
        bars.append(
            Bar(
                bar_id=generate_bar_id(symbol, "30m", timestamp_text, "unit_test"),
                symbol=symbol,
                timestamp=timestamp,
                interval="30m",
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1000 + index,
                vwap=close,
                source="unit_test",
            )
        )
    return bars


if __name__ == "__main__":
    unittest.main()

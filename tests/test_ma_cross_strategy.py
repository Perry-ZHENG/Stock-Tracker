import unittest
from datetime import UTC, datetime, timedelta

from stock_agent.bars.validation import generate_bar_id
from stock_agent.schemas import Bar
from stock_agent.strategies.ma_cross import (
    DEFAULT_MA_CROSS_PAIRS,
    MA_CROSS_STRATEGY_ID,
    generate_ma_cross_signals,
)


class MaCrossStrategyTests(unittest.TestCase):
    def test_default_pairs_match_p1_requirements(self) -> None:
        self.assertEqual(DEFAULT_MA_CROSS_PAIRS, ((3, 5), (5, 10), (10, 20)))

    def test_insufficient_warmup_generates_no_signal(self) -> None:
        bars = _bars_from_closes([10] * 20)

        signals = generate_ma_cross_signals(bars, pairs=[(10, 20)])

        self.assertEqual(signals, [])

    def test_golden_cross_generates_buy_watch_signal(self) -> None:
        bars = _bars_from_closes([10, 10, 10, 10, 10, 20])

        signals = generate_ma_cross_signals(bars, pairs=[(3, 5)])

        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.strategy_id, MA_CROSS_STRATEGY_ID)
        self.assertEqual(signal.direction, "buy_watch")
        self.assertIn("MA3 上穿 MA5", signal.reason)
        self.assertIn("黄金交叉", signal.reason)
        self.assertEqual(len(signal.source_bar_ids), 6)
        self.assertEqual(signal.source_bar_ids, [bar.bar_id for bar in bars])

    def test_death_cross_generates_sell_watch_signal(self) -> None:
        bars = _bars_from_closes([20, 20, 20, 20, 20, 10])

        signals = generate_ma_cross_signals(bars, pairs=[(3, 5)])

        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.direction, "sell_watch")
        self.assertIn("MA3 下穿 MA5", signal.reason)
        self.assertIn("死亡交叉", signal.reason)

    def test_groups_symbols_before_calculation(self) -> None:
        qqq_bars = _bars_from_closes([10, 10, 10, 10, 10, 20], symbol="QQQ")
        spy_bars = _bars_from_closes([20, 20, 20, 20, 20, 10], symbol="SPY")

        signals = generate_ma_cross_signals([*qqq_bars, *spy_bars], pairs=[(3, 5)])

        self.assertEqual([signal.symbol for signal in signals], ["QQQ", "SPY"])
        self.assertEqual([signal.direction for signal in signals], ["buy_watch", "sell_watch"])

    def test_invalid_pair_rejected(self) -> None:
        with self.assertRaises(ValueError):
            generate_ma_cross_signals(_bars_from_closes([10] * 6), pairs=[(5, 3)])


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
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=1000 + index,
                vwap=close,
                source="unit_test",
            )
        )
    return bars


if __name__ == "__main__":
    unittest.main()

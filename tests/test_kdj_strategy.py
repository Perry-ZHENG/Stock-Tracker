import unittest
from datetime import UTC, datetime, timedelta

from stock_agent.bars.validation import generate_bar_id
from stock_agent.config import DEFAULT_CONFIG
from stock_agent.schemas import Bar
from stock_agent.strategies.kdj import DEFAULT_KDJ_PARAMS, KDJ_STRATEGY_ID, generate_kdj_signals


class KdjStrategyTests(unittest.TestCase):
    def test_default_kdj_is_disabled_in_config(self) -> None:
        self.assertFalse(DEFAULT_CONFIG["strategies"]["kdj"]["enabled"])
        self.assertEqual(DEFAULT_KDJ_PARAMS, (9, 3, 3))

    def test_warmup_insufficient_generates_no_signal(self) -> None:
        bars = _bars_from_closes([10] * 9)

        signals = generate_kdj_signals(bars)

        self.assertEqual(signals, [])

    def test_golden_cross_generates_buy_watch_after_warmup(self) -> None:
        bars = _bars_from_closes([10, 10, 10, 10, 10, 10, 10, 10, 20, 21, 22, 23])

        signals = generate_kdj_signals(bars, window=5, k_smoothing=3, d_smoothing=3)

        self.assertGreaterEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.strategy_id, KDJ_STRATEGY_ID)
        self.assertEqual(signal.direction, "buy_watch")
        self.assertIn("K 上穿 D", signal.reason)
        self.assertIn("J=", signal.reason)
        self.assertEqual(len(signal.source_bar_ids), 6)

    def test_death_cross_generates_sell_watch_after_warmup(self) -> None:
        bars = _bars_from_closes([20, 20, 20, 20, 20, 20, 20, 20, 10, 9, 8, 7])

        signals = generate_kdj_signals(bars, window=5, k_smoothing=3, d_smoothing=3)

        self.assertGreaterEqual(len(signals), 1)
        self.assertEqual(signals[0].direction, "sell_watch")
        self.assertIn("K 下穿 D", signals[0].reason)

    def test_invalid_params_rejected(self) -> None:
        with self.assertRaises(ValueError):
            generate_kdj_signals(_bars_from_closes([10] * 10), window=0)


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

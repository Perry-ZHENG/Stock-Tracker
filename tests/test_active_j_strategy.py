import unittest
from datetime import UTC, datetime, timedelta

from stock_agent.bars.validation import generate_bar_id
from stock_agent.schemas import Bar
from stock_agent.strategies.active_j import (
    ACTIVE_J_STRATEGY_ID,
    DEFAULT_ACTIVE_J_MA_WINDOW,
    DEFAULT_ACTIVE_J_THRESHOLD,
    generate_active_j_signals,
)
from stock_agent.tracing import trace_for_signal


class ActiveJStrategyTests(unittest.TestCase):
    def test_defaults_match_t203_scope(self) -> None:
        self.assertEqual(DEFAULT_ACTIVE_J_THRESHOLD, 20.0)
        self.assertEqual(DEFAULT_ACTIVE_J_MA_WINDOW, 80)

    def test_warmup_insufficient_generates_no_signal(self) -> None:
        bars = _bars_from_closes([10] * 80)

        signals = generate_active_j_signals(bars)

        self.assertEqual(signals, [])

    def test_j_strength_generates_buy_watch_with_ma80_exit_reference(self) -> None:
        bars = _bars_from_closes([10] * 80 + [20])

        signals = generate_active_j_signals(bars)

        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.strategy_id, ACTIVE_J_STRATEGY_ID)
        self.assertEqual(signal.direction, "buy_watch")
        self.assertIn("KDJ(9,3,3)", signal.reason)
        self.assertIn("RSV=", signal.reason)
        self.assertIn("K=", signal.reason)
        self.assertIn("D=", signal.reason)
        self.assertIn("J=", signal.reason)
        self.assertIn("J_threshold=20.0000", signal.reason)
        self.assertIn("exit_reference=MA80", signal.reason)
        self.assertIn("MA80=", signal.reason)
        self.assertIn("support_line=disabled_v1", signal.reason)
        self.assertEqual(len(signal.source_bar_ids), 81)

    def test_j_below_threshold_generates_no_signal(self) -> None:
        bars = _bars_from_closes([20] * 80 + [10])

        signals = generate_active_j_signals(bars)

        self.assertEqual(signals, [])

    def test_signal_trace_preserves_source_bar_chain(self) -> None:
        signal = generate_active_j_signals(_bars_from_closes([10] * 80 + [20]))[0]

        trace = trace_for_signal(signal)

        self.assertEqual(trace.trace_id, signal.trace_id)
        self.assertEqual(trace.input_ref, signal.source_bar_ids)
        self.assertEqual(trace.output_ref, [signal.signal_id])

    def test_invalid_params_rejected(self) -> None:
        with self.assertRaises(ValueError):
            generate_active_j_signals(_bars_from_closes([10] * 81), ma_window=0)


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

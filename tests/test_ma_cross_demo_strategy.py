import json
import unittest
from pathlib import Path

from stock_agent.bars import BarBuilder
from stock_agent.providers.csv_demo import CsvDemoProvider
from stock_agent.schemas import Signal
from stock_agent.strategies.ma_cross_demo import (
    MA_CROSS_DEMO_STRATEGY_ID,
    generate_ma_cross_demo_signals,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_BARS_PATH = PROJECT_ROOT / "data" / "sample" / "sample_bars.csv"
EXPECTED_SIGNAL_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "expected_signals" / "ma_cross_demo_2_3.json"
)


class MaCrossDemoStrategyTests(unittest.TestCase):
    def test_sample_bars_generate_one_expected_signal(self) -> None:
        bars = BarBuilder().from_standard_bars(
            CsvDemoProvider(SAMPLE_BARS_PATH).fetch_intraday_bars()
        )
        expected_payload = json.loads(EXPECTED_SIGNAL_PATH.read_text(encoding="utf-8"))
        expected_signals = [Signal.model_validate(payload) for payload in expected_payload]

        signals = generate_ma_cross_demo_signals(bars)

        self.assertEqual(signals, expected_signals)

    def test_less_than_four_bars_generate_no_signal(self) -> None:
        bars = CsvDemoProvider(SAMPLE_BARS_PATH).fetch_intraday_bars()[:3]

        signals = generate_ma_cross_demo_signals(bars)

        self.assertEqual(signals, [])

    def test_strategy_id_is_demo_only(self) -> None:
        self.assertEqual(MA_CROSS_DEMO_STRATEGY_ID, "ma_cross_demo_2_3")

    def test_source_bar_ids_use_current_three_bar_window(self) -> None:
        bars = BarBuilder().from_standard_bars(
            CsvDemoProvider(SAMPLE_BARS_PATH).fetch_intraday_bars()
        )

        signal = generate_ma_cross_demo_signals(bars)[0]

        self.assertEqual(
            signal.source_bar_ids,
            [
                "QQQ-30m-2026-05-22T14:30:00Z-demo_csv",
                "QQQ-30m-2026-05-22T15:00:00Z-demo_csv",
                "QQQ-30m-2026-05-22T15:30:00Z-demo_csv",
            ],
        )


if __name__ == "__main__":
    unittest.main()

import csv
import json
import unittest
from pathlib import Path

from stock_agent.schemas import Signal


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_BARS_PATH = PROJECT_ROOT / "data" / "sample" / "sample_bars.csv"
EXPECTED_SIGNAL_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "expected_signals" / "ma_cross_demo_2_3.json"
)


class FixtureTests(unittest.TestCase):
    def test_sample_bars_fixture_exists_with_five_rows(self) -> None:
        with SAMPLE_BARS_PATH.open(newline="", encoding="utf-8") as csv_file:
            rows = list(csv.DictReader(csv_file))

        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["symbol"], "QQQ")
        self.assertEqual(rows[-1]["timestamp"], "2026-05-22T15:30:00Z")

    def test_sample_bars_fixture_has_required_columns(self) -> None:
        with SAMPLE_BARS_PATH.open(newline="", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)

            self.assertEqual(
                reader.fieldnames,
                [
                    "symbol",
                    "timestamp",
                    "interval",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "vwap",
                    "source",
                ],
            )

    def test_expected_signal_fixture_validates_against_signal_schema(self) -> None:
        payload = json.loads(EXPECTED_SIGNAL_PATH.read_text(encoding="utf-8"))

        self.assertEqual(len(payload), 1)
        signal = Signal.model_validate(payload[0])
        self.assertEqual(signal.strategy_id, "ma_cross_demo_2_3")
        self.assertEqual(signal.timestamp.isoformat(), "2026-05-22T15:30:00+00:00")
        self.assertEqual(signal.direction, "buy_watch")

    def test_expected_signal_uses_sample_bar_window(self) -> None:
        signal = json.loads(EXPECTED_SIGNAL_PATH.read_text(encoding="utf-8"))[0]

        self.assertEqual(
            signal["source_bar_ids"],
            [
                "QQQ-30m-2026-05-22T14:30:00Z-demo_csv",
                "QQQ-30m-2026-05-22T15:00:00Z-demo_csv",
                "QQQ-30m-2026-05-22T15:30:00Z-demo_csv",
            ],
        )


if __name__ == "__main__":
    unittest.main()

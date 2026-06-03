import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from stock_agent.providers.csv_demo import CsvDemoProvider, CsvDemoProviderError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_BARS_PATH = PROJECT_ROOT / "data" / "sample" / "sample_bars.csv"


class CsvDemoProviderTests(unittest.TestCase):
    def test_reads_sample_bars_as_standard_bars(self) -> None:
        provider = CsvDemoProvider(SAMPLE_BARS_PATH)

        bars = provider.fetch_intraday_bars()

        self.assertEqual(len(bars), 5)
        self.assertEqual(bars[0].bar_id, "QQQ-30m-2026-05-22T13:30:00Z-demo_csv")
        self.assertEqual(bars[0].symbol, "QQQ")
        self.assertEqual(bars[0].timestamp.tzinfo, UTC)
        self.assertEqual(bars[0].open, 100.0)
        self.assertEqual(bars[0].volume, 100000)
        self.assertEqual(bars[0].source, "demo_csv")
        self.assertEqual(bars[0].quality_flag, "normal")

    def test_filters_by_symbol_interval_and_time_range(self) -> None:
        provider = CsvDemoProvider(SAMPLE_BARS_PATH)

        bars = provider.fetch_intraday_bars(
            symbols=["QQQ"],
            interval="30m",
            start=datetime(2026, 5, 22, 14, 30, tzinfo=UTC),
            end=datetime(2026, 5, 22, 15, 0, tzinfo=UTC),
        )

        self.assertEqual([bar.timestamp.hour for bar in bars], [14, 15])

    def test_missing_file_raises_readable_error(self) -> None:
        provider = CsvDemoProvider(Path("missing.csv"))

        with self.assertRaisesRegex(CsvDemoProviderError, "file not found"):
            provider.fetch_intraday_bars()

    def test_missing_column_raises_readable_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = Path(tmp_dir) / "bad.csv"
            csv_path.write_text("symbol,timestamp\nQQQ,2026-05-22T13:30:00Z\n", encoding="utf-8")
            provider = CsvDemoProvider(csv_path)

            with self.assertRaisesRegex(CsvDemoProviderError, "missing columns"):
                provider.fetch_intraday_bars()

    def test_invalid_numeric_value_raises_readable_error_with_line_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_path = Path(tmp_dir) / "bad.csv"
            csv_path.write_text(
                "\n".join(
                    [
                        "symbol,timestamp,interval,open,high,low,close,volume,vwap,source",
                        "QQQ,2026-05-22T13:30:00Z,30m,not-a-number,100.50,99.80,100.00,100000,100.10,demo_csv",
                    ]
                ),
                encoding="utf-8",
            )
            provider = CsvDemoProvider(csv_path)

            with self.assertRaisesRegex(CsvDemoProviderError, "line 2"):
                provider.fetch_intraday_bars()


if __name__ == "__main__":
    unittest.main()

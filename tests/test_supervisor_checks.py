import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from stock_agent.bars import BarBuilder
from stock_agent.providers.csv_demo import CsvDemoProvider
from stock_agent.schemas import Bar, Signal
from stock_agent.storage.repositories import list_trace_chain
from stock_agent.storage.sqlite import initialize_database
from stock_agent.strategies.ma_cross_demo import generate_ma_cross_demo_signals
from stock_agent.supervisor.checks import signal_traces, supervise_candidate_signals
from stock_agent.tracing import trace_for_signal

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_BARS_PATH = PROJECT_ROOT / "data" / "sample" / "sample_bars.csv"
EXPECTED_SIGNAL_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "expected_signals" / "ma_cross_demo_2_3.json"
)


class SupervisorChecksTests(unittest.TestCase):
    def test_approves_expected_demo_signal(self) -> None:
        bars = _sample_bars()
        signals = generate_ma_cross_demo_signals(bars)
        expected = _expected_signals()

        result = supervise_candidate_signals(
            bars=bars,
            candidate_signals=signals,
            traces=signal_traces(signals),
            expected_signals=expected,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.approved_signals, expected)
        self.assertEqual(result.rejected_signals, [])

    def test_rejects_signal_when_trace_is_missing(self) -> None:
        bars = _sample_bars()
        signals = generate_ma_cross_demo_signals(bars)

        result = supervise_candidate_signals(
            bars=bars,
            candidate_signals=signals,
            traces=[],
            expected_signals=_expected_signals(),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.approved_signals, [])
        self.assertEqual(result.rejected_signals, signals)
        self.assertIn("missing trace", "; ".join(result.errors))

    def test_rejects_signal_when_warmup_is_insufficient(self) -> None:
        bars = _sample_bars()[:3]
        signal = _expected_signals()[0]

        result = supervise_candidate_signals(
            bars=bars,
            candidate_signals=[signal],
            traces=[trace_for_signal(signal)],
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.approved_signals, [])
        self.assertIn("requires at least 4 bars", "; ".join(result.errors))

    def test_rejects_abnormal_bar_and_persists_failure_trace(self) -> None:
        bars = _sample_bars()
        bad_bar = Bar(
            bar_id="QQQ-30m-2026-05-22T15:30:00Z-demo_csv",
            symbol="QQQ",
            timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
            interval="30m",
            open=471.0,
            high=470.0,
            low=472.0,
            close=471.5,
            volume=100,
            vwap=471.5,
            source="demo_csv",
        )
        candidate_signals = generate_ma_cross_demo_signals(bars)

        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "stock_agent.sqlite")

            result = supervise_candidate_signals(
                bars=[*bars[:4], bad_bar],
                candidate_signals=candidate_signals,
                traces=signal_traces(candidate_signals),
                connection=connection,
            )

            stored_traces = list_trace_chain(connection)
            connection.close()

        self.assertFalse(result.ok)
        self.assertEqual(result.approved_signals, [])
        self.assertIn("bar validation failed", "; ".join(result.errors))
        self.assertTrue(any(trace.status == "failed" and trace.module == "supervisor" for trace in stored_traces))
        self.assertTrue(any(trace.module == "supervisor_recompute" for trace in stored_traces))

    def test_rejects_expected_signal_regression_mismatch(self) -> None:
        bars = _sample_bars()
        signals = generate_ma_cross_demo_signals(bars)
        wrong_expected = [
            signals[0].model_copy(update={"reason": "wrong deterministic fixture"})
        ]

        result = supervise_candidate_signals(
            bars=bars,
            candidate_signals=signals,
            traces=signal_traces(signals),
            expected_signals=wrong_expected,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.approved_signals, [])
        self.assertIn("expected signal regression mismatch", result.errors)

    def test_no_signal_path_creates_skipped_trace_without_error(self) -> None:
        bars = _sample_bars()[:3]

        result = supervise_candidate_signals(
            bars=bars,
            candidate_signals=[],
            traces=[],
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.approved_signals, [])
        self.assertEqual(result.traces[0].status, "skipped")


def _sample_bars() -> list[Bar]:
    return BarBuilder().from_standard_bars(
        CsvDemoProvider(SAMPLE_BARS_PATH).fetch_intraday_bars()
    )


def _expected_signals() -> list[Signal]:
    payload = json.loads(EXPECTED_SIGNAL_PATH.read_text(encoding="utf-8"))
    return [Signal.model_validate(item) for item in payload]


if __name__ == "__main__":
    unittest.main()

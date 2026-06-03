import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from stock_agent.schemas import Signal
from stock_agent.tracing import failed_trace, skipped_trace, trace_for_signal


class TracingTests(unittest.TestCase):
    def test_trace_for_signal_links_signal_to_source_bars(self) -> None:
        signal = Signal(
            signal_id="sig-001",
            strategy_id="ma_cross_demo_2_3",
            symbol="QQQ",
            timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
            direction="buy_watch",
            strength=0.7,
            confidence=0.9,
            reason="MA2 crossed above MA3",
            trace_id="trace-sig-001",
            source_bar_ids=["bar-001", "bar-002", "bar-003"],
            data_quality="normal",
            created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
        )

        trace = trace_for_signal(signal)

        self.assertEqual(trace.trace_id, signal.trace_id)
        self.assertEqual(trace.module, "strategy_engine")
        self.assertEqual(trace.input_ref, signal.source_bar_ids)
        self.assertEqual(trace.output_ref, [signal.signal_id])
        self.assertEqual(trace.status, "success")

    def test_failed_trace_records_error_message(self) -> None:
        trace = failed_trace(
            trace_id="trace-failed-001",
            module="bar_validation",
            input_ref=["bar-bad"],
            error_msg="high below low",
            created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
        )

        self.assertEqual(trace.status, "failed")
        self.assertEqual(trace.error_msg, "high below low")
        self.assertEqual(trace.output_ref, [])

    def test_skipped_trace_records_reason(self) -> None:
        trace = skipped_trace(
            trace_id="trace-skipped-001",
            module="strategy_engine",
            input_ref=["bar-001"],
            reason="warm-up insufficient",
            created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
        )

        self.assertEqual(trace.status, "skipped")
        self.assertEqual(trace.error_msg, "warm-up insufficient")

    def test_invalid_trace_status_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            failed_trace(
                trace_id="trace-invalid",
                module="bar_validation",
                input_ref=[],
                error_msg="bad",
                created_at=datetime(2026, 5, 22, 15, 30),
            )


if __name__ == "__main__":
    unittest.main()

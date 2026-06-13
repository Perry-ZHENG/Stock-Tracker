import io
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from stock_agent.cli import main
from stock_agent.commands.trace import run_trace_query
from stock_agent.schemas import Signal, TraceChain
from stock_agent.storage.repositories import insert_signal, insert_trace_chain
from stock_agent.storage.sqlite import initialize_runtime_database


class TraceQueryTests(unittest.TestCase):
    def test_queries_trace_by_signal_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            signal = _signal()
            insert_signal(connection, signal)
            insert_trace_chain(connection, _trace(signal))
            connection.close()
            stream = io.StringIO()

            result = run_trace_query(root, signal.signal_id, stream=stream)

        self.assertTrue(result.ok)
        output = stream.getvalue()
        self.assertIn("trace_status=ok", output)
        self.assertIn("signal_id=sig-001", output)
        self.assertIn("strategy_id=ma_cross", output)
        self.assertIn("supervisor_decision=approved", output)
        self.assertIn("source_bar_ids=bar-001,bar-002,bar-003", output)
        self.assertIn("reason=MA3 crossed above MA5", output)
        self.assertIn("trace_input=bar-001,bar-002,bar-003", output)

    def test_queries_trace_by_trace_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            signal = _signal()
            insert_signal(connection, signal)
            insert_trace_chain(connection, _trace(signal))
            connection.close()
            stream = io.StringIO()

            result = run_trace_query(root, signal.trace_id, stream=stream)

        self.assertTrue(result.ok)
        self.assertIn("query_id=trace-sig-001", stream.getvalue())
        self.assertIn("signal_id=sig-001", stream.getvalue())

    def test_missing_trace_returns_readable_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_runtime_database(root).close()
            stream = io.StringIO()

            result = run_trace_query(root, "missing", stream=stream)

        self.assertFalse(result.ok)
        self.assertIn("trace_error=not found", stream.getvalue())

    def test_stock_agent_cli_trace_uses_real_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            signal = _signal()
            insert_signal(connection, signal)
            insert_trace_chain(connection, _trace(signal))
            connection.close()

            with patch("pathlib.Path.cwd", return_value=root):
                self.assertEqual(main(["cli", "trace", signal.signal_id]), 0)


def _signal() -> Signal:
    return Signal(
        signal_id="sig-001",
        strategy_id="ma_cross",
        symbol="QQQ",
        timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
        direction="buy_watch",
        strength=0.7,
        confidence=0.8,
        reason="MA3 crossed above MA5",
        trace_id="trace-sig-001",
        source_bar_ids=["bar-001", "bar-002", "bar-003"],
        data_quality="normal",
        created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
    )


def _trace(signal: Signal) -> TraceChain:
    return TraceChain(
        trace_id=signal.trace_id,
        parent_id=None,
        module="strategy_engine",
        input_ref=signal.source_bar_ids,
        output_ref=[signal.signal_id],
        status="success",
        error_msg=None,
        created_at=signal.created_at,
    )


if __name__ == "__main__":
    unittest.main()

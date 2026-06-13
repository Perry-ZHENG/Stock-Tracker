import io
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from stock_agent.commands.interactive_cli import run_interactive_cli
from stock_agent.schemas import Signal
from stock_agent.storage.repositories import insert_signal, list_config_changes
from stock_agent.storage.sqlite import initialize_runtime_database, open_database


class InteractiveCliTests(unittest.TestCase):
    def test_interactive_cli_runs_read_only_query_from_chinese_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            insert_signal(connection, _signal())
            connection.close()
            output = io.StringIO()

            exit_code = run_interactive_cli(
                root,
                input_stream=io.StringIO("最近 QQQ 有什么信号\nexit\n"),
                output_stream=output,
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("sig-001", output.getvalue())
        self.assertIn("bye", output.getvalue())

    def test_interactive_cli_records_confirmed_pending_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            initialize_runtime_database(root).close()
            output = io.StringIO()

            exit_code = run_interactive_cli(
                root,
                input_stream=io.StringIO("添加 QQQ 到关注\nyes\nexit\n"),
                output_stream=output,
            )
            connection = open_database(root / "data/runtime/stock_agent.sqlite")
            changes = list_config_changes(connection)
            connection.close()

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["status"], "pending_review")
        self.assertIn("requires CLI approve", output.getvalue())

    def test_interactive_cli_blocks_high_risk_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = io.StringIO()

            exit_code = run_interactive_cli(
                Path(tmp_dir),
                input_stream=io.StringIO("buy 10 shares of QQQ\nexit\n"),
                output_stream=output,
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("blocked=place_order", output.getvalue())
        self.assertIn("观察信号", output.getvalue())


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
        source_bar_ids=["bar-001"],
        data_quality="normal",
        created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
    )


if __name__ == "__main__":
    unittest.main()

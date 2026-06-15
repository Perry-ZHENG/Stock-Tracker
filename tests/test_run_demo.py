import io
import shutil
import tempfile
import unittest
from pathlib import Path

from stock_agent.commands.run_demo import run_demo
from stock_agent.storage.repositories import list_notifications, list_signals, list_trace_chain
from stock_agent.storage.sqlite import open_database

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RunDemoTests(unittest.TestCase):
    def test_run_demo_persists_expected_signal_trace_and_notification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _copy_demo_inputs(root)
            stream = io.StringIO()

            summary = run_demo(root, stream=stream)
            connection = open_database(root / "data/runtime/stock_agent.sqlite")

            signals = list_signals(connection)
            traces = list_trace_chain(connection)
            notifications = list_notifications(connection)
            connection.close()

        self.assertEqual(summary.bars_read, 5)
        self.assertEqual(summary.bars_used, 5)
        self.assertEqual(summary.candidate_signals, 1)
        self.assertEqual(summary.approved_signals, 1)
        self.assertEqual(summary.rejected_signals, 0)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].signal_id, "sig-qqq-ma2-ma3-20260522T153000Z")
        self.assertEqual(len(traces), 2)
        self.assertIn(signals[0].trace_id, {trace.trace_id for trace in traces})
        self.assertEqual(len(notifications), 1)
        self.assertIn("Run demo summary", stream.getvalue())


def _copy_demo_inputs(root: Path) -> None:
    shutil.copytree(PROJECT_ROOT / "data" / "sample", root / "data" / "sample")
    expected_target = root / "tests" / "fixtures" / "expected_signals"
    expected_target.mkdir(parents=True)
    shutil.copy2(
        PROJECT_ROOT / "tests" / "fixtures" / "expected_signals" / "ma_cross_demo_2_3.json",
        expected_target / "ma_cross_demo_2_3.json",
    )


if __name__ == "__main__":
    unittest.main()

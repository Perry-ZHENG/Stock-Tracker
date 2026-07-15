import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from stock_agent.storage.repositories import list_signals, list_trace_chain
from stock_agent.storage.sqlite import open_database

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EndToEndRegressionTests(unittest.TestCase):
    def test_worker_once_regression_config_produces_expected_formal_strategy_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _copy_regression_inputs(root)

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "stock_agent.cli",
                    "worker",
                    "--once",
                    "--include-legacy-market-watch",
                    "--interval-sec",
                    "0.01",
                    "--config",
                    "tests/fixtures/configs/regression.yaml",
                ],
                cwd=root,
                env=_subprocess_env(),
                text=True,
                capture_output=True,
                check=False,
            )

            connection = open_database(root / "data/runtime/regression.sqlite")
            try:
                signals = list_signals(connection, limit=100)
                traces = list_trace_chain(connection, limit=200)
            finally:
                connection.close()

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("worker_status=completed", completed.stdout)
        self.assertIn("approved_signals=36", completed.stdout)
        self.assertEqual(
            Counter(signal.strategy_id for signal in signals),
            {
                "active_j": 7,
                "boll": 3,
                "kdj": 9,
                "ma_cross": 9,
                "macd": 8,
            },
        )
        traces_by_id = {trace.trace_id: trace for trace in traces}
        signals_by_id = {signal.signal_id: signal for signal in signals}
        for expected in _expected_signals():
            signal = signals_by_id[expected["signal_id"]]
            self.assertEqual(signal.strategy_id, expected["strategy_id"])
            self.assertEqual(signal.trace_id, expected["trace_id"])
            self.assertEqual(signal.source_bar_ids, expected["source_bar_ids"])
            self.assertIn(signal.trace_id, traces_by_id)


def _copy_regression_inputs(root: Path) -> None:
    shutil.copytree(PROJECT_ROOT / "data" / "sample", root / "data" / "sample")
    config_target = root / "tests" / "fixtures" / "configs"
    config_target.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "tests" / "fixtures" / "configs" / "regression.yaml", config_target / "regression.yaml")


def _expected_signals() -> list[dict[str, object]]:
    path = PROJECT_ROOT / "tests" / "fixtures" / "expected_signals" / "formal_regression.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("STOCK_AGENT_CONFIG", None)
    env.pop("STOCK_AGENT_WORKDIR", None)
    python_path = str(PROJECT_ROOT / "src")
    if env.get("PYTHONPATH"):
        python_path = python_path + os.pathsep + env["PYTHONPATH"]
    env["PYTHONPATH"] = python_path
    env["STOCK_AGENT_NOW"] = "2026-05-22T15:30:00Z"
    return env


if __name__ == "__main__":
    unittest.main()

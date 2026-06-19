import io
import shutil
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from stock_agent.cli import main
from stock_agent.commands.worker import run_worker
from stock_agent.config import DEFAULT_CONFIG, render_config_yaml
from stock_agent.storage.repositories import list_health_metrics
from stock_agent.storage.repositories import list_notifications, list_signals, list_strategy_snapshots
from stock_agent.storage.sqlite import initialize_runtime_database, open_database
from stock_agent.worker import SingleInstanceLock, SingleInstanceLockError, Worker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MARKET_OPEN_NOW = datetime(2026, 5, 22, 15, 30, tzinfo=UTC)


class WorkerTests(unittest.TestCase):
    def test_single_instance_lock_rejects_duplicate_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "worker.lock"
            first = SingleInstanceLock(lock_path)
            first.acquire()
            try:
                with self.assertRaises(SingleInstanceLockError):
                    SingleInstanceLock(lock_path).acquire()
            finally:
                first.release()

    def test_worker_once_writes_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            worker = Worker(
                connection,
                lock_path=root / "data/runtime/stock_agent.worker.lock",
                interval_sec=0.01,
            )

            result = worker.run(once=True)
            metrics = list_health_metrics(connection)
            connection.close()

        self.assertEqual(result.ticks, 1)
        self.assertFalse(result.stopped)
        self.assertEqual(metrics[0].module, "worker")
        self.assertEqual(metrics[0].status, "healthy")

    def test_worker_run_command_once_outputs_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _copy_sample_data(root)
            stream = io.StringIO()

            exit_code = run_worker(root, once=True, interval_sec=0.01, stream=stream, now_fn=lambda: MARKET_OPEN_NOW)
            connection = open_database(root / "data/runtime/stock_agent.sqlite")
            metrics = list_health_metrics(connection)
            connection.close()

        self.assertEqual(exit_code, 0)
        self.assertIn("worker_status=completed", stream.getvalue())
        self.assertIn("ticks=1", stream.getvalue())
        self.assertIn("last_tick_summary:", stream.getvalue())
        self.assertEqual(metrics[0].module, "worker")

    def test_worker_command_respects_single_instance_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            lock_path = root / "data/runtime/stock_agent.worker.lock"
            lock = SingleInstanceLock(lock_path)
            lock.acquire()
            stream = io.StringIO()
            try:
                exit_code = run_worker(root, once=True, stream=stream)
            finally:
                lock.release()

        self.assertEqual(exit_code, 1)
        self.assertIn("worker_status=already_running", stream.getvalue())

    def test_worker_stop_flag_returns_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            worker = Worker(
                connection,
                lock_path=root / "data/runtime/stock_agent.worker.lock",
                interval_sec=0.01,
            )
            worker.request_stop()

            result = worker.run()
            connection.close()

        self.assertEqual(result.ticks, 0)
        self.assertTrue(result.stopped)

    def test_stock_agent_worker_once_uses_real_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _copy_sample_data(root)
            with patch("pathlib.Path.cwd", return_value=root):
                self.assertEqual(main(["worker", "--once", "--interval-sec", "0.01"]), 0)

    def test_worker_uses_stock_agent_config_storage_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _copy_sample_data(root)
            config = deepcopy(DEFAULT_CONFIG)
            config["storage"]["sqlite_path"] = "custom/runtime.sqlite"
            config_path = root / "custom" / "config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(render_config_yaml(config), encoding="utf-8")
            stream = io.StringIO()

            with patch.dict("os.environ", {"STOCK_AGENT_CONFIG": str(config_path)}):
                exit_code = run_worker(root, once=True, interval_sec=0.01, stream=stream)

            connection = open_database(root / "custom/runtime.sqlite")
            metrics = list_health_metrics(connection)
            connection.close()

        self.assertEqual(exit_code, 0)
        self.assertIn("worker_status=completed", stream.getvalue())
        self.assertEqual(metrics[0].module, "worker")
        self.assertFalse((root / "data/runtime/stock_agent.sqlite").exists())

    def test_worker_pipeline_persists_signal_snapshot_notification_and_lake(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _copy_sample_data(root)
            config = deepcopy(DEFAULT_CONFIG)
            config["symbols"]["default"] = ["QQQ"]
            config["strategies"]["ma_cross"]["enabled"] = True
            config["strategies"]["ma_cross"]["pairs"] = [[2, 3]]
            config["strategies"]["boll"]["enabled"] = False
            config_path = root / "configs" / "config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(render_config_yaml(config), encoding="utf-8")
            stream = io.StringIO()

            exit_code = run_worker(root, once=True, interval_sec=0.01, stream=stream, now_fn=lambda: MARKET_OPEN_NOW)
            connection = open_database(root / "data/runtime/stock_agent.sqlite")
            signals = list_signals(connection)
            snapshots = list_strategy_snapshots(connection)
            notifications = list_notifications(connection)
            connection.close()
            lake_file_exists = (root / "data/lake/raw_bars/date=2026-05-22/part-00000.jsonl").exists()

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].strategy_id, "ma_cross")
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(len(notifications), 1)
        self.assertTrue(lake_file_exists)
        self.assertIn("approved_signals=1", stream.getvalue())

    def test_worker_pipeline_does_not_duplicate_sent_notification_on_repeated_tick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _copy_sample_data(root)
            config = deepcopy(DEFAULT_CONFIG)
            config["symbols"]["default"] = ["QQQ"]
            config["strategies"]["ma_cross"]["enabled"] = True
            config["strategies"]["ma_cross"]["pairs"] = [[2, 3]]
            config["strategies"]["boll"]["enabled"] = False
            config_path = root / "configs" / "config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(render_config_yaml(config), encoding="utf-8")

            first_exit = run_worker(root, once=True, interval_sec=0.01, stream=io.StringIO(), now_fn=lambda: MARKET_OPEN_NOW)
            second_stream = io.StringIO()
            second_exit = run_worker(root, once=True, interval_sec=0.01, stream=second_stream, now_fn=lambda: MARKET_OPEN_NOW)
            connection = open_database(root / "data/runtime/stock_agent.sqlite")
            notifications = list_notifications(connection)
            connection.close()

        self.assertEqual(first_exit, 0)
        self.assertEqual(second_exit, 0)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["status"], "sent")
        self.assertIn("notifications=0", second_stream.getvalue())


def _copy_sample_data(root: Path) -> None:
    shutil.copytree(PROJECT_ROOT / "data" / "sample", root / "data" / "sample")


if __name__ == "__main__":
    unittest.main()

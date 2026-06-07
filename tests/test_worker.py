import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_agent.cli import main
from stock_agent.commands.worker import run_worker
from stock_agent.storage.repositories import list_health_metrics
from stock_agent.storage.sqlite import initialize_runtime_database, open_database
from stock_agent.worker import SingleInstanceLock, SingleInstanceLockError, Worker


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

        self.assertEqual(result.ticks, 1)
        self.assertFalse(result.stopped)
        self.assertEqual(metrics[0].module, "worker")
        self.assertEqual(metrics[0].status, "healthy")

    def test_worker_run_command_once_outputs_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            stream = io.StringIO()

            exit_code = run_worker(root, once=True, interval_sec=0.01, stream=stream)
            connection = open_database(root / "data/runtime/stock_agent.sqlite")
            metrics = list_health_metrics(connection)

        self.assertEqual(exit_code, 0)
        self.assertIn("worker_status=completed", stream.getvalue())
        self.assertIn("ticks=1", stream.getvalue())
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

        self.assertEqual(result.ticks, 0)
        self.assertTrue(result.stopped)

    def test_stock_agent_worker_once_uses_real_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            with patch("pathlib.Path.cwd", return_value=root):
                self.assertEqual(main(["worker", "--once", "--interval-sec", "0.01"]), 0)


if __name__ == "__main__":
    unittest.main()

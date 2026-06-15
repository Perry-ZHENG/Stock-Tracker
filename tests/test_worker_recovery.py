import tempfile
import unittest
from pathlib import Path

from stock_agent.storage.repositories import get_checkpoint, list_notifications
from stock_agent.storage.sqlite import initialize_database
from stock_agent.worker.pipeline import WorkerTickSummary
from stock_agent.worker.recovery import CrashBudgetExceeded, CrashRecoveryManager
from stock_agent.worker.scheduler import Worker


class WorkerRecoveryTests(unittest.TestCase):
    def test_crash_budget_records_and_stops_at_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.sqlite")
            manager = CrashRecoveryManager(connection, crash_limit=3)

            manager.record_crash("first")
            manager.record_crash("second")
            with self.assertRaises(CrashBudgetExceeded):
                manager.record_crash("third")
            state = manager.state()
            notifications = list_notifications(connection)
            connection.close()

        self.assertEqual(state.crash_count, 3)
        self.assertTrue(state.stopped)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["payload"]["type"], "worker_failure")

    def test_worker_stops_instead_of_restarting_forever(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_database(root / "runtime.sqlite")
            worker = Worker(
                connection,
                lock_path=root / "worker.lock",
                interval_sec=0,
                pipeline=_FailingPipeline(),
                recovery_manager=CrashRecoveryManager(connection, crash_limit=2),
            )

            result = worker.run(max_ticks=10)
            checkpoint = get_checkpoint(connection, "worker:crash_budget")
            connection.close()

        self.assertTrue(result.stopped)
        self.assertGreaterEqual(len(result.errors), 2)
        self.assertIn("crash_count=2", checkpoint["checkpoint_value"])

    def test_recovery_budget_sends_failure_notification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.sqlite")
            manager = CrashRecoveryManager(connection, recovery_limit=2)

            manager.record_recovery_attempt("previous crash")
            with self.assertRaises(CrashBudgetExceeded):
                manager.record_recovery_attempt("previous crash")
            notifications = list_notifications(connection)
            connection.close()

        self.assertEqual(len(notifications), 1)
        self.assertIn("recovery budget exceeded", notifications[0]["payload"]["message"])


class _FailingPipeline:
    def run_once(self) -> WorkerTickSummary:
        raise RuntimeError("boom")


if __name__ == "__main__":
    unittest.main()

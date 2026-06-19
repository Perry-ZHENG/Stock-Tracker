import io
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from stock_agent.config import DEFAULT_CONFIG, render_config_yaml
from stock_agent.storage.repositories import list_health_metrics, list_notifications
from stock_agent.storage.sqlite import open_database
from stock_agent.worker.identity import WorkerIdentity, build_worker_identity
from stock_agent.worker.scheduler import SingleInstanceLock, SingleInstanceLockError
from stock_agent.commands.worker import run_worker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MARKET_OPEN_NOW = datetime(2026, 5, 22, 15, 30, tzinfo=UTC)


class WorkerIdentityTests(unittest.TestCase):
    def test_lock_file_records_instance_identity_and_blocks_second_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "worker.lock"
            identity = WorkerIdentity(instance_id="inst-1", host_id="host-1")
            lock = SingleInstanceLock(lock_path, identity=identity)
            lock.acquire()
            content = lock_path.read_text(encoding="utf-8")

            with self.assertRaises(SingleInstanceLockError):
                SingleInstanceLock(lock_path, identity=identity).acquire()
            lock.release()

        self.assertIn("instance_id=inst-1", content)
        self.assertIn("host_id=host-1", content)
        self.assertIn("lock_owner=host-1:inst-1", content)

    def test_build_identity_defaults_multi_instance_disabled(self) -> None:
        with patch.dict("os.environ", {"STOCK_AGENT_INSTANCE_ID": "demo-inst", "STOCK_AGENT_HOST_ID": "demo-host"}, clear=True):
            identity = build_worker_identity()

        self.assertEqual(identity.instance_id, "demo-inst")
        self.assertEqual(identity.host_id, "demo-host")
        self.assertFalse(identity.multi_instance_enabled)

    def test_worker_health_and_notification_include_instance_without_duplicate_notification(self) -> None:
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

            with patch.dict("os.environ", {"STOCK_AGENT_INSTANCE_ID": "inst-a", "STOCK_AGENT_HOST_ID": "host-a"}):
                self.assertEqual(
                    run_worker(root, once=True, interval_sec=0.01, stream=io.StringIO(), now_fn=lambda: MARKET_OPEN_NOW),
                    0,
                )
                self.assertEqual(
                    run_worker(root, once=True, interval_sec=0.01, stream=io.StringIO(), now_fn=lambda: MARKET_OPEN_NOW),
                    0,
                )

            connection = open_database(root / "data/runtime/stock_agent.sqlite")
            metrics = list_health_metrics(connection, limit=10)
            notifications = list_notifications(connection)
            connection.close()

        self.assertTrue(any(metric.details.get("instance_id") == "inst-a" for metric in metrics))
        cli_notifications = [row for row in notifications if row["channel"] == "cli"]
        self.assertEqual(len(cli_notifications), 1)
        self.assertEqual(cli_notifications[0]["payload"]["instance_id"], "inst-a")


def _copy_sample_data(root: Path) -> None:
    source = PROJECT_ROOT / "data" / "sample" / "sample_bars.csv"
    target = root / "data" / "sample" / "sample_bars.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()

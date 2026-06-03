import io
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.commands.health import run_health
from stock_agent.health import HealthThresholds, classify_health_status, record_health_metric
from stock_agent.storage.sqlite import initialize_database


class HealthMonitorTests(unittest.TestCase):
    def test_classifies_healthy_status(self) -> None:
        now = datetime(2026, 5, 22, 15, 30, tzinfo=UTC)

        status = classify_health_status(
            now=now,
            heartbeat_at=now - timedelta(seconds=30),
            data_latency_sec=10,
            error_rate=0,
            consecutive_failures=0,
        )

        self.assertEqual(status, "healthy")

    def test_classifies_degraded_status(self) -> None:
        now = datetime(2026, 5, 22, 15, 30, tzinfo=UTC)

        status = classify_health_status(
            now=now,
            heartbeat_at=now,
            data_latency_sec=60,
            error_rate=0,
            consecutive_failures=0,
        )

        self.assertEqual(status, "degraded")

    def test_classifies_unhealthy_status(self) -> None:
        now = datetime(2026, 5, 22, 15, 30, tzinfo=UTC)

        status = classify_health_status(
            now=now,
            heartbeat_at=now - timedelta(seconds=301),
            data_latency_sec=1,
            error_rate=0,
            consecutive_failures=0,
            thresholds=HealthThresholds(heartbeat_timeout_sec=300),
        )

        self.assertEqual(status, "unhealthy")

    def test_record_health_metric_persists_metric(self) -> None:
        now = datetime(2026, 5, 22, 15, 30, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "stock_agent.sqlite")

            metric = record_health_metric(
                connection,
                module="run_demo",
                now=now,
                data_latency_sec=1,
                error_rate=0,
                consecutive_failures=0,
            )

            self.assertEqual(metric.status, "healthy")

    def test_health_command_reads_latest_metric(self) -> None:
        now = datetime(2026, 5, 22, 15, 30, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_database(root / "data/runtime/stock_agent.sqlite")
            record_health_metric(connection, module="run_demo", now=now)
            stream = io.StringIO()

            result = run_health(root, stream=stream)

        self.assertEqual(result.status, "healthy")
        self.assertIn("health_status=healthy", stream.getvalue())
        self.assertIn("module=run_demo", stream.getvalue())

    def test_health_command_reports_missing_database_unhealthy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            stream = io.StringIO()

            result = run_health(Path(tmp_dir), stream=stream)

        self.assertEqual(result.status, "unhealthy")
        self.assertIn("error=no runtime database", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
